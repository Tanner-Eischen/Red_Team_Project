"""Reproduce all-family Docker quality controls, with no model profiles or provider calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from harborforge.authoring.provenance import read_manifest
from harborforge.storage.registry import redact
from harborforge.task_families.registry import FAMILIES, generate_task
from harborforge.validation.quality_gates import validate_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES, action="append")
    parser.add_argument("--seed", type=int, default=20261101)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    families = args.family or list(FAMILIES)
    results = []
    for family in families:
        try:
            task = generate_task(family, args.seed, output / "tasks")
            manifest = read_manifest(task)
            evidence = validate_task(task, runtime="docker", evidence_dir=output / "validation" / family)
            results.append({
                "family": family, "task_version_id": manifest["task_version_id"],
                "task_sha256": manifest["artifact_sha256"], "runtime": evidence.get("runtime"),
                "valid": evidence.get("valid") is True,
                "eligible_for_model_evaluation": evidence.get("eligible_for_model_evaluation") is True,
                "evidence_path": f"validation/{family}/validation.json",
                "controls": [{"name": gate["name"], "passed": gate["passed"]} for gate in evidence["gates"]],
            })
        except Exception as error:
            results.append({"family": family, "valid": False, "eligible_for_model_evaluation": False,
                            "error_type": type(error).__name__, "error": redact(str(error))})
    files = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in (output / "validation").rglob("*.json") if path.is_file() and not path.is_symlink()}
    summary = {"schema_version": 1, "recorded_utc": datetime.now(timezone.utc).isoformat(),
               "model_attempts": 0, "model_calls": 0,
               "scope": "Actual Docker author/negative quality controls, not model capability measurements",
               "valid": all(row["valid"] and row["eligible_for_model_evaluation"] for row in results),
               "families": results, "evidence_json_sha256": files}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
