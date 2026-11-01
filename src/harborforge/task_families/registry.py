"""Generate self-contained Harbor packages from implemented task families."""
from __future__ import annotations

import json
from pathlib import Path
import re
import textwrap

from harborforge.authoring.provenance import task_digest
from . import scientific_etl, repository_debug, geospatial_raster

FAMILIES = ("scientific-etl", "geospatial-raster", "repository-debug")
ALIASES = {"python-data-validation": "scientific-etl", "software-debugging": "repository-debug",
           "scientific_etl": "scientific-etl", "geospatial_raster": "geospatial-raster",
           "repository_debug": "repository-debug"}


def _write(path: Path, content: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


def _json(path, value):
    _write(path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _installer(source: str, input_name: str) -> str:
    return textwrap.dedent(f'''\
        import argparse
        from pathlib import Path
        import subprocess
        import sys

        parser = argparse.ArgumentParser()
        parser.add_argument('--workspace', type=Path, required=True)
        args = parser.parse_args()
        args.workspace.mkdir(parents=True, exist_ok=True)
        target = args.workspace / 'pipeline.py'
        target.write_text({source!r}, encoding='utf-8')
        result = subprocess.run([sys.executable, str(target), '--input',
                                 str(args.workspace / {input_name!r}), '--output',
                                 str(args.workspace / 'output')], check=False)
        raise SystemExit(result.returncode)
        ''')


def _task_toml(artifacts: list[str]) -> str:
    return textwrap.dedent(f'''\
        schema_version = "1.3"
        artifacts = {json.dumps(artifacts)}

        [agent]
        timeout_sec = 600.0
        user = "agent"
        network_mode = "no-network"

        [verifier]
        timeout_sec = 180.0
        environment_mode = "separate"
        user = "root"
        network_mode = "no-network"

        [environment]
        build_timeout_sec = 600.0
        cpus = 1
        memory_mb = 2048
        storage_mb = 4096
        network_mode = "no-network"
        ''')


def generate_task(family: str, seed: int, output_dir: Path | str, *, cohort: str = "discovery", version: int = 1,
                  parent: dict | None = None, mutation: dict | str | None = None) -> Path:
    family = ALIASES.get(family, family)
    if family not in FAMILIES:
        raise ValueError(f"Unsupported task family {family!r}; choose {', '.join(FAMILIES)}")
    if cohort not in ("discovery", "calibration", "holdout"):
        raise ValueError("Unknown cohort")
    if type(seed) is not int or seed < 0 or type(version) is not int or version < 1:
        raise ValueError("seed must be nonnegative and version must be positive integers")
    if parent:
        if cohort != "discovery" or parent.get("cohort") != "discovery":
            raise ValueError("Only discovery tasks can have adaptive revisions")
        if parent.get("family") != family or parent.get("seed") != seed or version != parent.get("version", 0) + 1:
            raise ValueError("A revision must preserve family and seed and increment the parent version once")
    task_id = parent["task_id"] if parent else f"{family}-{seed}"
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", task_id):
        raise ValueError("Unsafe task ID")
    task = Path(output_dir) / f"{task_id}-v{version}"
    if task.exists():
        raise FileExistsError(f"Immutable task already exists: {task}")
    module = {"scientific-etl": scientific_etl, "repository-debug": repository_debug,
              "geospatial-raster": geospatial_raster}[family]
    level = version - 1
    cases = []
    input_name = "input" if family == "geospatial-raster" else (
        "input/observations.csv" if family == "scientific-etl" else "input/intervals.json")
    # Preflight optional binary dependency before creating any output directory.
    if family == "geospatial-raster":
        try:
            import rasterio  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Install compatible geo dependencies with pip install '.[geo]'") from exc
    for index, current_seed in enumerate((seed, seed + 101, seed + 202)):
        fixture_dir = task / "tests" / "fixtures" / str(index)
        if family == "scientific-etl":
            csv_text, expected = module.fixture(current_seed, level)
            _write(fixture_dir / "observations.csv", csv_text)
            case = dict(kind="etl", filename="observations.csv", output="summary.json", expected=expected)
        elif family == "repository-debug":
            value, expected = module.fixture(current_seed, level)
            _json(fixture_dir / "intervals.json", value)
            case = dict(kind="repository", filename="intervals.json", output="result.json", input=value,
                        expected=expected)
        else:
            expected = module.fixture(fixture_dir, current_seed, level)
            case = dict(kind="raster", expected=expected)
        cases.append(case)
    if family == "repository-debug":
        for value, expected in (([], {"intervals": [], "covered": 0}),
                                ([[5, -2], [0, 2]], {"error": "reversed_interval"}),
                                ([[0, 2], [2, 4], [4, 4]], {"intervals": [[0, 4]], "covered": 4})):
            index = len(cases)
            _json(task / "tests" / "fixtures" / str(index) / "intervals.json", value)
            cases.append(dict(kind="repository", filename="intervals.json", output="result.json", input=value,
                              expected=expected))
    import shutil
    shutil.copytree(task / "tests" / "fixtures" / "0", task / "environment" / "public" / "input")
    _write(task / "environment" / "public" / "pipeline.py", module.BUGGY)
    _write(task / "environment" / "public" / "README.md", module.INSTRUCTION)
    _write(task / "instruction.md", module.INSTRUCTION)
    _write(task / "solution" / "solve.py", _installer(module.SOURCE, input_name))
    _write(task / "solution" / "solve.sh", "#!/usr/bin/env bash\nset -euo pipefail\npython3 /solution/solve.py --workspace /app\n")
    _write(task / "controls" / "alternate.py", _installer(module.ALTERNATE, input_name))
    _write(task / "controls" / "bad.py", _installer(module.BUGGY, input_name))
    # An additional negative is not merely the no-op: output hardcoding passes
    # the public fixture but fails unseen behavioral fixtures.
    if family != "geospatial-raster":
        key = "summary.json" if family == "scientific-etl" else "result.json"
        hardcoded = ("import argparse,json\nfrom pathlib import Path\n"
                     "p=argparse.ArgumentParser();p.add_argument('--input');p.add_argument('--output');a=p.parse_args()\n"
                     "o=Path(a.output);o.mkdir(parents=True,exist_ok=True)\n"
                     f"(o/{key!r}).write_text({json.dumps(cases[0]['expected'])!r},encoding='utf-8')\n")
        _write(task / "controls" / "hardcoded.py", _installer(hardcoded, input_name))
    else:
        _write(task / "controls" / "hardcoded.py", _installer(module.SOURCE.replace(
            "resampling=Resampling.nearest", "resampling=Resampling.bilinear"), input_name))
    _write(task / "tests" / "verify.py", Path(__file__).with_name("verifier_template.py").read_text(encoding="utf-8"))
    _json(task / "tests" / "cases.json", cases)
    _write(task / "tests" / "test.sh", '''#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
chmod 700 /tests /logs/verifier
rm -f /logs/verifier/reward.txt
set +e
python3 /tests/verify.py --workspace /app
status=$?
set -e
if [ "$status" -eq 0 ]; then
    printf '1\\n' > /logs/verifier/reward.txt
elif [ "$status" -eq 1 ]; then
    printf '0\\n' > /logs/verifier/reward.txt
else
    exit "$status"
fi
''')
    extra = "RUN python -m pip install --no-cache-dir numpy==2.2.6 rasterio==1.4.3\n" if family == "geospatial-raster" else ""
    _write(task / "environment" / "Dockerfile", "FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c\n"
           "RUN apt-get update && apt-get install -y --no-install-recommends bash tmux && rm -rf /var/lib/apt/lists/*\n"
           + extra + "RUN useradd --create-home --uid 1000 agent\nWORKDIR /app\nCOPY public/ /app/\n"
           "RUN chown -R agent:agent /app\nUSER agent\nCMD [\"/bin/bash\"]\n")
    _write(task / "task.toml", _task_toml(["/app/pipeline.py", "/app/output"]))
    manifest = dict(schema_version="1.0", task_id=task_id, task_version_id=f"{task_id}:v{version}", version=version,
                    family=family, seed=seed, cohort=cohort, protocol="terminal_agent",
                    parent_task_version_id=parent.get("task_version_id") if parent else None,
                    mutation=mutation, source="synthetic-generated-fixture", review_status="pending",
                    public_artifacts=["/app/pipeline.py", "/app/output"],
                    controls={"oracle": "solution/solve.py", "alternate": "controls/alternate.py",
                              "bad": "controls/bad.py", "mutations": ["controls/hardcoded.py"]},
                    verifier="tests/verify.py", requirements=module.INSTRUCTION.strip().splitlines())
    _json(task / "manifest.json", manifest)
    manifest["artifact_sha256"] = task_digest(task)
    _json(task / "manifest.json", manifest)
    return task
