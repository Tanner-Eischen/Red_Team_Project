"""Build reproducible public fixtures and optional local control evidence, with zero model calls."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from harborforge.analysis.reporting import build_report
from harborforge.authoring.provenance import read_manifest
from harborforge.storage.registry import Registry, utc_now
from harborforge.task_families.registry import FAMILIES, generate_task
from harborforge.validation.quality_gates import validate_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("tasks/public_examples"))
    parser.add_argument("--evidence-dir", type=Path, default=Path("artifacts/public-pilot"))
    parser.add_argument("--report-dir", type=Path, default=Path("reports/public-pilot"))
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    paths = []
    for family in FAMILIES:
        for seed in range(100, 104):
            path = args.output_dir / f"{family}-{seed}-v1"
            if path.exists():
                manifest = read_manifest(path)
                if manifest["family"] != family or manifest["seed"] != seed or manifest["cohort"] != "discovery":
                    raise ValueError("Existing example differs from public pilot design")
            else:
                path = generate_task(family, seed, args.output_dir)
            paths.append(path.resolve())
    if not args.validate:
        print(json.dumps({"tasks": list(map(str, paths)), "model_calls": 0}, indent=2))
        return 0
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    config = {"campaign_id": "public-local-controls-v1", "objective": "adversarial_discovery",
              "protocol": "terminal_agent", "cohort": "discovery", "tasks": list(map(str, paths)),
              "model_profiles": [], "include_timeouts_in_denominator": False,
              "purpose": "Local author control validation only; zero model attempts"}
    validations = []
    with Registry(args.evidence_dir / "registry.sqlite") as registry:
        registry.create_campaign(config)
        for path in paths:
            manifest = read_manifest(path)
            registry.register_task(manifest, path)
            evidence = validate_task(path, runtime="local", evidence_dir=args.evidence_dir / path.name)
            registry.record_validation(manifest["task_version_id"], evidence)
            validations.append({"task_version_id": manifest["task_version_id"],
                "family": manifest["family"], "artifact_sha256": manifest["artifact_sha256"],
                "runtime": "local", "valid": evidence["valid"], "eligible_for_model_evaluation": False,
                "gates": [{"name": gate["name"], "passed": gate["passed"]} for gate in evidence["gates"]]})
            print(f"{path.name}: {'PASS' if evidence['valid'] else 'FAIL'} local controls", flush=True)
        report = build_report(registry, config["campaign_id"], args.report_dir)
    summary = {"checked_utc": utc_now(), "model_attempts": 0,
               "container_isolation_verified": False, "local_controls_only": True,
               "task_count": len(validations), "valid_tasks": sum(v["valid"] for v in validations),
               "family_counts": dict(Counter(v["family"] for v in validations)), "tasks": validations}
    (args.report_dir / "local-controls.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report), "valid_tasks": summary["valid_tasks"], "model_attempts": 0}))
    return 0 if all(v["valid"] for v in validations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
