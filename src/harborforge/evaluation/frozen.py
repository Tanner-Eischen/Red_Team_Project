"""Immutable suite manifests with task drift and preregistration checks."""

from __future__ import annotations

import json
from pathlib import Path

from harborforge.storage.registry import canonical_json, config_digest, redact, utc_now


def preregistration_config(config: dict) -> dict:
    """The suite binds task contents; its own path cannot participate in its hash."""
    return {key: value for key, value in config.items() if key not in {"suite_manifest", "tasks"}}


def _check_preregistration(preregistration: dict) -> None:
    if not isinstance(preregistration, dict) or not preregistration:
        raise ValueError("A nonempty experiment preregistration is required")
    if redact(preregistration) != preregistration:
        raise ValueError("Preregistration must not contain credentials")
    for name in ("protocol", "model_profiles", "harness_profile"):
        if name not in preregistration:
            raise ValueError(f"Preregistration must declare {name}")
    if preregistration["protocol"] not in {"terminal_agent", "qa_completion"}:
        raise ValueError("Unsupported protocol")
    if preregistration.get("objective") != "fixed_evaluation":
        raise ValueError("Frozen evaluation requires an explicit fixed_evaluation objective")
    if preregistration.get("discovery_policy") is not None:
        raise ValueError("Frozen evaluation cannot enable an adaptive discovery policy")
    if preregistration.get("cohort") not in {"discovery", "calibration", "holdout"}:
        raise ValueError("Frozen evaluation requires an explicit cohort")
    models = preregistration["model_profiles"]
    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        raise ValueError("Preregistered models must be profile mappings")
    identifiers = set()
    for model in models:
        identifier = model.get("profile_id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError("Preregistered model profile IDs must be nonempty and unique")
        identifiers.add(identifier)
        if any(not isinstance(model.get(key), str) or not model[key].strip() for key in ("provider", "model_id")):
            raise ValueError("Preregistered models must bind provider and model identity, not a profile name alone")
        repeats = model.get("repeats", preregistration.get("repeats"))
        if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
            raise ValueError("Each model must preregister positive repeats")
    timeout_policy = preregistration.get("include_timeouts_in_denominator", preregistration.get("include_timeouts"))
    if not isinstance(timeout_policy, bool):
        raise ValueError("Timeout denominator policy must be explicit")
    harness = preregistration["harness_profile"]
    if not isinstance(harness, dict) or not isinstance(harness.get("profile_id"), str) or not harness["profile_id"].strip():
        raise ValueError("Harness must be a profile mapping with a nonempty profile ID")
    if preregistration["protocol"] == "terminal_agent" and harness.get("harbor_version") != "0.13.2":
        raise ValueError("Terminal evaluation must freeze the supported Harbor version 0.13.2")
    if not preregistration["model_profiles"] or not harness:
        raise ValueError("Model and harness configurations must be frozen before evaluation")
    if preregistration.get("objective") == "fixed_evaluation" and preregistration.get(
        "budgets", {}
    ).get("max_task_revisions", 0):
        raise ValueError("Fixed evaluation cannot enable task revisions")
    if preregistration.get("cohort") == "holdout" and preregistration.get("objective") != "fixed_evaluation":
        raise ValueError("Holdout requires fixed evaluation")


def freeze_suite(task_paths: list[Path | str], output_path: Path | str, preregistration: dict) -> dict:
    from harborforge.authoring.provenance import task_digest

    preregistration = preregistration_config(preregistration)
    _check_preregistration(preregistration)
    if not task_paths:
        raise ValueError("Cannot freeze an empty suite")
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError("Frozen suite already exists; create a new version")
    entries = []
    cohorts = set()
    ids = set()
    for task_path in task_paths:
        path = Path(task_path).resolve()
        if output.is_relative_to(path):
            raise ValueError("Frozen suite must be stored outside authored task directories")
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        digest = task_digest(path)
        if manifest["artifact_sha256"] != digest:
            raise ValueError("Task changed since it was authored")
        if manifest["task_version_id"] in ids:
            raise ValueError("Suite contains a duplicate task version")
        if manifest["protocol"] != preregistration["protocol"]:
            raise ValueError("Task protocol differs from preregistration")
        cohorts.add(manifest["cohort"])
        ids.add(manifest["task_version_id"])
        entries.append({"task_version_id": manifest["task_version_id"], "artifact_sha256": digest,
                        "task_path": str(path), "family": manifest["family"], "cohort": manifest["cohort"]})
    if len(cohorts) != 1:
        raise ValueError("A frozen suite cannot mix discovery, calibration and holdout tasks")
    if preregistration.get("cohort") is not None and preregistration["cohort"] != next(iter(cohorts)):
        raise ValueError("Suite task cohort differs from preregistration")
    suite = {"schema_version": 1, "created_utc": utc_now(), "cohort": next(iter(cohorts)),
             "preregistration": preregistration, "preregistration_sha256": config_digest(preregistration),
             "tasks": sorted(entries, key=lambda item: item["task_version_id"])}
    suite["suite_sha256"] = config_digest(suite)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(suite, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return suite


def verify_suite(path: Path | str, preregistration: dict | None = None) -> dict:
    from harborforge.authoring.provenance import task_digest

    suite = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = suite.get("suite_sha256")
    content = {key: value for key, value in suite.items() if key != "suite_sha256"}
    if config_digest(content) != expected:
        raise ValueError("Frozen suite manifest hash mismatch")
    _check_preregistration(suite["preregistration"])
    if config_digest(suite["preregistration"]) != suite["preregistration_sha256"]:
        raise ValueError("Preregistration hash mismatch")
    if preregistration is not None and canonical_json(preregistration_config(preregistration)) != canonical_json(
        suite["preregistration"]
    ):
        raise ValueError("Evaluation configuration differs from frozen preregistration")
    if not suite.get("tasks") or suite.get("cohort") != suite["preregistration"]["cohort"]:
        raise ValueError("Frozen suite must retain its nonempty preregistered cohort")
    ids = set()
    for entry in suite["tasks"]:
        if entry["task_version_id"] in ids:
            raise ValueError("Frozen suite contains duplicate task versions")
        ids.add(entry["task_version_id"])
        task_path = Path(entry["task_path"])
        manifest = json.loads((task_path / "manifest.json").read_text(encoding="utf-8"))
        if task_digest(task_path) != entry["artifact_sha256"] or manifest.get("artifact_sha256") != entry["artifact_sha256"]:
            raise ValueError(f"Frozen task drift: {entry['task_version_id']}")
        if manifest["task_version_id"] != entry["task_version_id"] or manifest["cohort"] != suite["cohort"]:
            raise ValueError("Frozen task identity/cohort mismatch")
        if (entry.get("cohort") != suite["cohort"] or manifest["protocol"] != suite["preregistration"]["protocol"]
                or manifest["family"] != entry.get("family")):
            raise ValueError("Frozen task family/protocol/cohort metadata mismatch")
    return suite
