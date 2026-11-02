"""Run the owner-authorized frozen calibration pilot with no positive-price route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
import os

from harborforge.config import load_campaign
from harborforge.evaluation.frozen import verify_suite
from probe_openrouter_free import load_existing_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = load_campaign(args.campaign)
    if (config.objective != "fixed_evaluation" or config.cohort != "calibration"
            or config.budgets.campaign_usd_max != 0 or not config.model_profiles
            or any(model.billing_mode != "free_only" for model in config.model_profiles)):
        raise ValueError("This launcher accepts only the authorized zero-price calibration matrix")
    verify_suite(config.suite_manifest, config.model_dump(mode="json"))
    cap = config.budgets.max_model_attempts * max(
        model.free_routing.max_requests_per_attempt for model in config.model_profiles
    )
    if cap > 216:
        raise ValueError("Initial pilot exceeds its 216-request envelope")
    load_existing_key(args.key_file)
    request = Request("https://openrouter.ai/api/v1/key", headers={
        "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})
    with urlopen(request, timeout=30) as response:
        quota = json.load(response)["data"]["free_model_daily_requests"]
    if type(quota.get("remaining")) is not int or quota["remaining"] < cap:
        raise ValueError("The account lacks the complete bounded pilot request allowance")
    print(json.dumps({"status": "starting_free_calibration", "maximum_requests": cap,
                      "free_requests_remaining": quota["remaining"], "model_usd_cap": 0}), flush=True)
    from harborforge.execution.campaign import run_campaign
    from harborforge.analysis.reporting import build_report
    from harborforge.storage.registry import Registry

    registry_path = args.output_dir.parent / "registry.sqlite"
    summary = run_campaign(config, registry_path, args.output_dir, fixed=True)
    with Registry(registry_path) as registry:
        build_report(registry, config.campaign_id, args.output_dir.parent / "private-report", public=False)
        trials = registry.list_trials(config.campaign_id)
        observed = {outcome: sum(trial["outcome"] == outcome for trial in trials)
                    for outcome in sorted({trial["outcome"] for trial in trials})}
    print(json.dumps({"summary": summary, "observed_outcomes": observed}, indent=2), flush=True)
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
