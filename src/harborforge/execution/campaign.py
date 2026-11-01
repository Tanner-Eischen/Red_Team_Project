"""One bounded campaign controller shared by discovery and fixed evaluation."""

from __future__ import annotations

import json
import hashlib
import math
import time
import uuid
from pathlib import Path
from typing import Any

from harborforge.authoring.provenance import task_digest
from harborforge.config import CampaignConfig
from harborforge.execution.budget import BudgetExceeded, BudgetLedger
from harborforge.storage.registry import Registry, utc_now


def _manifest(path: Path, cohort: str) -> dict:
    value = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if value["cohort"] != cohort:
        raise ValueError("task cohort differs from campaign; holdout cannot enter discovery")
    if value["protocol"] != "terminal_agent":
        raise ValueError("terminal campaign cannot evaluate QA-completion tasks")
    if value["artifact_sha256"] != task_digest(path):
        raise ValueError("task package changed since authoring")
    return value


def _normalize_result(result: Any) -> dict:
    """A malformed executor response is evidence loss, never model failure."""
    if not isinstance(result, dict):
        result = {"outcome": "UNSCORABLE", "error_code": "INVALID_ADAPTER_RESPONSE"}
    result = dict(result)
    outcomes = {"PASS", "MODEL_FAIL", "AGENT_TIMEOUT", "PROVIDER_ERROR", "INFRA_ERROR", "INVALID_TASK", "UNSCORABLE"}
    reward = result.get("reward")
    if result.get("outcome") not in outcomes or (result.get("outcome") in {"PASS", "MODEL_FAIL"} and (
            isinstance(reward, bool) or reward not in (0, 1) or
            (result["outcome"] == "PASS") != (reward == 1))):
        result.update(outcome="UNSCORABLE", error_code="INVALID_ADAPTER_RESPONSE")
    if result["outcome"] not in {"PASS", "MODEL_FAIL"}:
        result["reward"] = None
    for key in ("cost_usd", "tokens_input", "tokens_output", "wall_time_s"):
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            result[key] = None
    return result


def run_campaign(config: CampaignConfig, registry_path: Path | str, output_dir: Path | str,
                 *, adapter: Any = None, validator: Any = None, fixed: bool = False) -> dict:
    """Record every attempted execution. Injected adapters are for contract tests only."""
    from harborforge.adapters.harbor_adapter import HarborAdapter
    from harborforge.validation.quality_gates import validate_task

    if (config.objective == "fixed_evaluation") != fixed:
        raise ValueError("use eval run for fixed evaluation and campaign run for discovery")
    paths = [Path(p).resolve() for p in config.tasks]
    if fixed:
        from harborforge.evaluation.frozen import preregistration_config, verify_suite

        suite = verify_suite(config.suite_manifest, preregistration_config(config.model_dump(mode="json")))
        paths = [Path(item["task_path"]) for item in suite["tasks"]]
        if config.tasks and set(map(str, paths)) != set(config.tasks):
            raise ValueError("configured tasks differ from frozen suite")
    if not paths:
        raise ValueError("campaign requires authored tasks")
    manifests = {str(path): _manifest(path, config.cohort) for path in paths}
    if len({m["task_version_id"] for m in manifests.values()}) != len(paths):
        raise ValueError("the same task version cannot occur twice in a campaign")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    adapter = adapter or HarborAdapter()
    validator = validator or validate_task
    ledger = BudgetLedger(config.budgets)
    config_dict = config.model_dump(mode="json")
    (output / "campaign.json").write_text(json.dumps(config_dict, indent=2), encoding="utf-8")
    summary = {"campaign_id": config.campaign_id, "started_utc": utc_now(), "status": "complete",
               "stop_reason": None, "quality_valid": 0, "quality_invalid": 0, "quality_errors": 0,
               "revisions": [], "promotions": [], "output_dir": str(output)}
    stop = False
    revisions = 0
    revised_parents: set[str] = set()
    validation_cache: dict[str, tuple[dict, str]] = {}
    with Registry(registry_path) as registry:
        campaign_id = registry.create_campaign(config_dict)
        if registry.list_trials(campaign_id):
            raise ValueError("campaign already has trials; choose a new preregistered campaign ID")
        registry.claim_campaign(campaign_id)
        harness = config.harness_profile.model_dump(mode="json")
        harness_id = registry.register_profile("harness", harness)
        model_ids = {model.profile_id: registry.register_profile("model", model.model_dump(mode="json"))
                     for model in config.model_profiles}
        for path in paths:
            registry.register_task(manifests[str(path)], path)

        def quality(path: Path) -> tuple[dict, dict, str]:
            manifest = _manifest(path, config.cohort)
            task_id = registry.register_task(manifest, path)
            if task_id not in validation_cache:
                try:
                    remaining = config.budgets.wall_time_seconds_max - (time.monotonic() - ledger.started)
                    if remaining <= 0:
                        raise TimeoutError("campaign wall-time exhausted before validation")
                    validation = validator(path, runtime="docker", timeout_seconds=remaining,
                                           evidence_dir=output / "validation" / hashlib.sha256(task_id.encode()).hexdigest()[:24])
                except Exception as error:
                    validation = {"valid": False, "runtime": "docker", "task_sha256": task_digest(path),
                                  "status": "VALIDATION_ERROR",
                                  "error": type(error).__name__ + ": " + str(error)}
                # No local test or injected 'passed' flag silently certifies container execution.
                validation = {**validation, "task_sha256": validation.get("task_sha256") or manifest["artifact_sha256"]}
                if validation.get("runtime") != "docker" or validation.get("eligible_for_model_evaluation") is not True:
                    validation = {**validation, "valid": False, "eligibility_error": "container validation required"}
                validation_id = registry.record_validation(task_id, validation)
                validation_cache[task_id] = (validation, validation_id)
                key = ("quality_valid" if validation.get("valid") else "quality_errors"
                       if validation.get("status") == "VALIDATION_ERROR" else "quality_invalid")
                summary[key] += 1
            return manifest, *validation_cache[task_id]

        def attempt(path: Path, manifest: dict, validation: dict, validation_id: str,
                    model: Any, repeat: int, retry: int) -> dict | None:
            nonlocal stop
            trial_id = "trial-" + uuid.uuid4().hex
            if not validation.get("valid"):
                outcome = "INFRA_ERROR" if validation.get("status") == "VALIDATION_ERROR" else "INVALID_TASK"
                result = {"outcome": outcome, "reward": None, "error_code": "quality_gate_failed",
                          "wall_time_s": 0, "cost_usd": 0, "tokens_input": 0, "tokens_output": 0}
            else:
                try:
                    if task_digest(path) != manifest["artifact_sha256"]:
                        raise ValueError("task package changed after quality validation")
                    reservation = ledger.reserve(model, harness["trial_time_limit_sec"])
                except BudgetExceeded as error:
                    stop = True
                    summary.update(status="budget_stopped", stop_reason=str(error))
                    return None
                started = time.monotonic()
                try:
                    result = adapter.run(path, model.model_dump(mode="json"), harness, output / "trials" / trial_id)
                except Exception as error:
                    result = {"outcome": "INFRA_ERROR", "reward": None, "cost_usd": None,
                              "tokens_input": None, "tokens_output": None, "error_code": type(error).__name__,
                              "error": str(error), "wall_time_s": time.monotonic() - started}
                result = _normalize_result(result)
                ledger.settle(reservation, result)
                if task_digest(path) != manifest["artifact_sha256"]:
                    result = {**result, "outcome": "INVALID_TASK", "reward": None,
                              "error_code": "task_changed_during_trial"}
            record = {**result, "trial_id": trial_id, "campaign_id": campaign_id,
                      "task_version_id": manifest["task_version_id"], "validation_id": validation_id,
                      "model_profile_id": model_ids[model.profile_id], "harness_profile_id": harness_id,
                      "repeat_index": repeat, "attempt_number": retry}
            trace_files = []
            trial_output = output / "trials" / trial_id
            if trial_output.is_dir():
                for trace_file in sorted(trial_output.rglob("*")):
                    if trace_file.is_file() and not trace_file.is_symlink():
                        stored = registry.store_artifact(trace_file, artifact_type="trace", visibility="private")
                        trace_files.append({"path": trace_file.relative_to(trial_output).as_posix(), **stored})
            if trace_files:
                artifact = registry.store_artifact(json.dumps(trace_files, sort_keys=True).encode(),
                                                    artifact_type="trace_manifest", visibility="private")
                record["trace_artifact_sha256"] = artifact["sha256"]
                record["trace_files"] = trace_files
            registry.record_trial(record)
            registry.append_event(trial_id, "final_outcome", record)
            registry.export_events(trial_id, output / "events" / f"{trial_id}.jsonl")
            if ledger.uncertain:
                stop = True
                summary.update(status="usage_reconciliation_required", stop_reason="unknown or over-envelope usage")
            return record

        if not config.model_profiles:
            for path in paths:
                quality(path)
            summary.update(status="validation_only", stop_reason="no model profiles configured; no inference requested")

        active = paths
        for tier in sorted({m.tier for m in config.model_profiles}):
            if stop:
                break
            next_tier: list[Path] = []
            for initial in active:
                path = initial
                lineage_depth = 0
                while not stop:
                    manifest, validation, validation_id = quality(path)
                    task_id = manifest["task_version_id"]
                    if tier == 3 and config.promotion.require_human_review_for_tier3 and \
                            task_id not in config.promotion.human_reviewed_task_ids and not fixed:
                        summary["promotions"].append({"task": task_id, "tier": tier, "eligible": False,
                                                      "reason": "human review required"})
                        break
                    results = []
                    for model in (m for m in config.model_profiles if m.tier == tier):
                        for repeat in range(model.repeats):
                            for retry in range(config.budgets.retry_provider_errors + 1):
                                if stop:
                                    break
                                result = attempt(path, manifest, validation, validation_id, model, repeat, retry)
                                if result is not None:
                                    results.append(result)
                                if result is None or result["outcome"] != "PROVIDER_ERROR":
                                    break
                            if stop:
                                break
                        if stop:
                            break
                    failures = [r for r in results if r["outcome"] == "MODEL_FAIL"]
                    informative = (len(failures) >= config.promotion.minimum_model_failures and
                                   validation.get("valid") and not any(r["outcome"] in
                                   {"INFRA_ERROR", "PROVIDER_ERROR", "UNSCORABLE", "INVALID_TASK"} for r in results))
                    if fixed:
                        next_tier.append(path)
                        break
                    summary["promotions"].append({"task": task_id, "tier": tier, "eligible": bool(informative),
                                                  "failures": len(failures), "attempts": len(results)})
                    if informative:
                        next_tier.append(path)
                    if not informative or stop or task_id in revised_parents or \
                            manifest["version"] - 1 >= config.budgets.max_task_revisions or \
                            revisions >= config.budgets.max_task_revisions or \
                            lineage_depth >= config.budgets.max_task_revisions:
                        break
                    from harborforge.authoring.mutations import revise_task

                    child = revise_task(path, output / "revisions", max_iterations=config.budgets.max_task_revisions)
                    child_manifest = _manifest(child, "discovery")
                    summary["revisions"].append({"parent": task_id, "child": child_manifest["task_version_id"],
                                                  "evidence_trials": [r["trial_id"] for r in failures]})
                    for failure in failures:
                        registry.append_event(failure["trial_id"], "revision_proposed", summary["revisions"][-1])
                    path = child
                    revised_parents.add(task_id)
                    revisions += 1
                    lineage_depth += 1
                if stop:
                    break
            active = list(dict.fromkeys(next_tier))
        for recorded in registry.list_trials(campaign_id):
            registry.export_events(recorded["trial_id"], output / "events" / f"{recorded['trial_id']}.jsonl")
    if summary["quality_invalid"] and summary["status"] in {"complete", "validation_only"}:
        summary["status"] = "quality_failed"
    if summary["quality_errors"] and summary["status"] in {"complete", "validation_only", "quality_failed"}:
        summary["status"] = "validation_error"
    summary.update(ended_utc=utc_now(), budget=ledger.snapshot())
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
