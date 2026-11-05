"""Create a fresh, frozen $0 OpenRouter calibration matrix without making model calls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shutil
from urllib.parse import quote
from urllib.request import urlopen

import yaml

from harborforge.config import CampaignConfig, HarnessProfile, load_campaign
from harborforge.evaluation.frozen import freeze_suite, verify_suite
from harborforge.task_families.registry import FAMILIES, generate_task

MODELS = (
    (1, "cohere/north-mini-code:free", "cohere"),
    (2, "nvidia/nemotron-3.5-super-vl:free", "nemotron"),
    (3, "nvidia/nemotron-3-ultra-550b-a55b:free", "nemotron"),
)
FLINT = ((1, "recursive/flint-v1.0:free", "recursive"),)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_public_evidence(evidence: Path, models=MODELS) -> None:
    """Fetch public model prices only; account settings require separate verified evidence."""
    urls = {"catalog.json": "https://openrouter.ai/api/v1/models"}
    urls.update({slug.replace("/", "__").replace(":", "_") + "-endpoints.json":
                 "https://openrouter.ai/api/v1/models/" + quote(slug, safe="/") + "/endpoints"
                 for _, slug, _ in models})
    payloads = {}
    for name, url in urls.items():
        with urlopen(url, timeout=30) as response:
            payloads[name] = json.load(response)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = evidence / "retrieval-history" / stamp
    archive.mkdir(parents=True)
    for name, payload in payloads.items():
        previous = evidence / name
        if previous.exists():
            shutil.copyfile(previous, archive / name)
        previous.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    (evidence / "retrieval.json").write_text(json.dumps({
        "verified_at_utc": datetime.now(timezone.utc).isoformat(), "source": "Official public OpenRouter APIs",
        "model_inference_requests": 0,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")


def _reference_campaign(path: Path) -> tuple[CampaignConfig, dict]:
    """Validate the earlier preregistration and task bytes without rewriting them."""
    reference = load_campaign(path)
    if (reference.objective != "fixed_evaluation" or reference.cohort != "calibration"
            or not reference.include_timeouts_in_denominator or len(reference.tasks) != 3
            or not reference.model_profiles or reference.budgets.campaign_usd_max != 0
            or any(model.billing_mode != "free_only" or model.repeats != 2
                   or model.agent_kwargs != {"reasoning_effort": "low", "temperature": 0.0}
                   or model.free_routing.max_requests_per_attempt != 12
                   or model.free_routing.minimum_request_interval_seconds != 4.0
                   for model in reference.model_profiles)):
        raise ValueError("Flint supplement requires the matched zero-cost three-task calibration reference")
    suite = verify_suite(reference.suite_manifest, reference.model_dump(mode="json"))
    if ({entry["task_path"] for entry in suite["tasks"]} != set(reference.tasks)
            or {entry["family"] for entry in suite["tasks"]} != set(FAMILIES)):
        raise ValueError("Reference task paths/families differ from the frozen calibration suite")
    return reference, suite


def prepare(evidence: Path, output: Path, seed: int = 20261102,
            flint_supplement_from: Path | None = None) -> Path:
    from harborforge.adapters.openrouter_free import verify_free_evidence

    reference, reference_suite = _reference_campaign(flint_supplement_from) if flint_supplement_from else (None, None)
    models = FLINT if reference else MODELS
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = evidence.resolve()
    catalog_path = output / "catalog.json"
    account_path = output / "account-safety.json"
    for name in ("catalog.json", "account-safety.json"):
        shutil.copyfile(evidence / name, output / name)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    snapshot = json.loads((evidence / "retrieval.json").read_text(encoding="utf-8"))
    verified = snapshot["verified_at_utc"]
    profiles = []
    for tier, slug, family in models:
        row = next(model for model in catalog["data"] if model["id"] == slug)
        endpoint_name = slug.replace("/", "__").replace(":", "_") + "-endpoints.json"
        endpoints = output / endpoint_name
        shutil.copyfile(evidence / endpoint_name, endpoints)
        model_id = "openrouter/" + slug
        canonical = row["canonical_slug"]
        policy = dict(policy_version="openrouter-free-only-v1", allowed_model_ids=[model_id],
            response_model_ids=list(dict.fromkeys([slug, slug.removesuffix(":free"), canonical, canonical + ":free"])),
            catalog_path=str(catalog_path), catalog_sha256=digest(catalog_path),
            endpoints_path=str(endpoints), endpoints_sha256=digest(endpoints),
            account_evidence_path=str(account_path), account_evidence_sha256=digest(account_path),
            verified_at_utc=verified, max_age_seconds=3600, max_requests_per_attempt=12,
            minimum_request_interval_seconds=4.0,
            provider_max_price={"prompt": 0.0, "completion": 0.0, "request": 0.0, "image": 0.0},
            allow_fallbacks=False, allow_paid_fallback=False, max_usd_per_request=0.0)
        verify_free_evidence(model_id, policy)
        profile_id = f"free-flint-{stamp}-output8192" if reference else f"free-tier-{tier}-20261102-output8192"
        profiles.append(dict(profile_id=profile_id, provider="openrouter", model_id=model_id,
            family=family, snapshot=canonical, tier=tier, repeats=2, supports_tools=True,
            billing_mode="free_only", free_routing=policy, max_cost_usd_per_attempt=0.0,
            max_input_tokens=256000, max_output_tokens=98304, input_usd_per_million=0.0,
            output_usd_per_million=0.0, price_source="https://openrouter.ai/api/v1/models/" + slug + "/endpoints",
            price_checked_utc=verified, agent_kwargs={"reasoning_effort": "low", "temperature": 0.0}))
    tasks = ([Path(path) for path in reference.tasks] if reference else
             [generate_task(family, seed, output / "tasks", cohort="calibration") for family in FAMILIES])
    if importlib.metadata.version("harbor") != "0.13.2":
        raise ValueError("Pilot requires the pinned Harbor runtime")
    harbor_root = Path(importlib.util.find_spec("harbor").origin).parent
    protocol_assets = {name: digest(harbor_root / name) for name in (
        "agents/terminus_2/templates/terminus-json-plain.txt",
        "agents/terminus_2/terminus_json_plain_parser.py",
        "llms/lite_llm.py",
    )}
    protocol_hash = hashlib.sha256(json.dumps(protocol_assets, sort_keys=True).encode()).hexdigest()
    (output / "harness-provenance.json").write_text(json.dumps({
        "harbor_version": "0.13.2", "assets_sha256": protocol_assets,
        "tool_schema_hash_definition": "SHA256 of sorted JSON mapping exact JSON-plain parser/template/formatter paths to raw SHA256",
        "tool_schema_sha256": protocol_hash, "provider_function_tools": False,
        "format_policy": "Same prompt-only JSON protocol for all models; no provider-specific schema enforcement",
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    harness = HarnessProfile.model_validate(dict(profile_id="harbor-terminus-0.13.2-openrouter-free-json8192-v3",
        max_input_tokens=256000, max_output_tokens=98304, per_call_output_tokens=8192,
        timeout_seconds=600, verifier_timeout_seconds=180, trial_time_limit_sec=1200,
        system_prompt_sha256=protocol_assets["agents/terminus_2/templates/terminus-json-plain.txt"],
        tool_schema_sha256=protocol_hash))
    if reference and reference.harness_profile != harness:
        raise ValueError("Installed harness/protocol differs from the frozen reference")
    planned_trials = len(tasks) * sum(profile["repeats"] for profile in profiles)
    campaign = CampaignConfig.model_validate(dict(campaign_id=("free-flint-" + stamp if reference else
        "free-pilot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")),
        objective="fixed_evaluation", cohort="calibration", tasks=list(map(str, tasks)),
        suite_manifest=str(output / "frozen-suite.json"), model_profiles=profiles,
        include_timeouts_in_denominator=True,
        harness_profile=harness,
        budgets=dict(campaign_usd_max=0.0, total_input_tokens_max=planned_trials * harness.max_input_tokens,
                     total_output_tokens_max=planned_trials * harness.max_output_tokens,
                     wall_time_seconds_max=planned_trials * harness.trial_time_limit_sec,
                     max_concurrency=1, max_model_attempts=planned_trials,
                     retry_provider_errors=0, max_provider_retries_total=0, max_task_revisions=0)))
    path = output / "campaign.yaml"
    path.write_text(yaml.safe_dump(campaign.model_dump(mode="json"), sort_keys=False), encoding="utf-8", newline="\n")
    freeze_suite(tasks, output / "frozen-suite.json", campaign.model_dump(mode="json"))
    if reference:
        (output / "reference-link.json").write_text(json.dumps({
            "reference_campaign_id": reference.campaign_id,
            "reference_campaign_sha256": digest(flint_supplement_from),
            "reference_suite_sha256": reference_suite["suite_sha256"],
            "tasks": reference_suite["tasks"],
            "harness_matched": True, "sampling_matched": True,
            "scope": "Supplementary model on the same calibration tasks; not a simultaneous or holdout comparison",
        }, indent=2) + "\n", encoding="utf-8", newline="\n")
    (output / "authorization.json").write_text(json.dumps({
        "date": datetime.now(timezone.utc).date().isoformat(),
        "scope": "Owner-requested Flint supplement" if reference else "Owner-requested initial OpenRouter free-only pilot",
        "maximum_model_cost_usd": 0, "planned_trials": planned_trials, "maximum_inference_requests": planned_trials * 12,
        "paid_routes_authorized": False, "automatic_retries": False,
        "note": "Three-tier roles are selection hypotheses, not measured universal model rankings.",
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--refresh-catalog", action="store_true", help="Refresh public price/endpoint metadata only")
    parser.add_argument("--flint-supplement-from", type=Path,
                        help="Run Flint only, reusing a verified reference campaign's exact calibration tasks/harness")
    args = parser.parse_args()
    if args.refresh_catalog:
        refresh_public_evidence(args.evidence_dir, FLINT if args.flint_supplement_from else MODELS)
    print(prepare(args.evidence_dir, args.output_dir, args.seed, args.flint_supplement_from))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
