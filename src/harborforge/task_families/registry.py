"""Generate self-contained Harbor packages from implemented task families."""
from __future__ import annotations

import json
import hashlib
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


def _public_instruction(instruction: str) -> str:
    """Publish enforced limits in both channels visible to an evaluated agent."""
    return instruction.rstrip() + "\n\n" + textwrap.dedent("""\
        ## Runtime and artifact limits

        These limits apply to the public run and every unseen behavioral case.
        Python 3.12 is installed. ETL and interval tasks use the standard library;
        raster tasks also have NumPy 2.2.6 and rasterio 1.4.3 preinstalled.
        Network access is disabled; do not depend on downloading packages or data.

        The container has 1 CPU, 2 GiB RAM and a 64-process PID ceiling. Its root
        filesystem is read-only. Writable tmpfs capacities are /app 32 MiB,
        /tmp 64 MiB, /home/agent 32 MiB and /logs 16 MiB, shared with that RAM
        allowance. The nominal task storage request is not extra writable space.
        Use /app/output for submitted output; only /app/pipeline.py and
        /app/output are transferred. Source must be self-contained apart from the
        declared preinstalled libraries. Each transfer permits at most 128 paths
        (including directories), 8 MiB per file and 16 MiB total file contents.

        Submitted source and output files must be regular, singly linked files:
        no symbolic links, hard links or special files. pipeline.py must be
        strictly smaller than 1 MiB (1048576 bytes). Each JSON result may be at
        most 1 MiB (inclusive); each output GeoTIFF must be strictly smaller than
        1 MiB. Keep output directories free of links and special files.

        Every protected behavioral execution has a 20-second wall limit,
        15-second CPU limit, 1 GiB address-space limit, 16-process limit,
        64-file-descriptor limit and 8 MiB per-file write limit. Container file
        writes otherwise have a 16 MiB per-file ceiling. Descendants are stopped
        before grading reads results. Grading runs under a different user;
        private grading files and reward paths are not writable by the candidate.
        During behavioral checks, source, input and the current working directory
        are read-only; place scratch files in /tmp or the supplied --output
        directory.
        The same invocation is repeated to check overwrite/idempotence behavior.
        """)


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
    if version > 11:
        raise ValueError("Family version 2 supports at most ten bounded revisions")
    if parent:
        if cohort != "discovery" or parent.get("cohort") != "discovery":
            raise ValueError("Only discovery tasks can have adaptive revisions")
        if parent.get("family") != family or parent.get("seed") != seed or version != parent.get("version", 0) + 1:
            raise ValueError("A revision must preserve family and seed and increment the parent version once")
    task_id = parent["task_id"] if parent else f"{family}-f2-{seed}"
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", task_id):
        raise ValueError("Unsafe task ID")
    task = Path(output_dir) / f"{task_id}-v{version}"
    if task.exists():
        raise FileExistsError(f"Immutable task already exists: {task}")
    module = {"scientific-etl": scientific_etl, "repository-debug": repository_debug,
              "geospatial-raster": geospatial_raster}[family]
    level = version - 1
    from harborforge.authoring.mutations import CATALOG, MUTATIONS
    operator_id = mutation.get("operator") if isinstance(mutation, dict) else mutation
    variant = operator_id if operator_id in CATALOG else None
    if mutation is not None and operator_id not in CATALOG and operator_id not in MUTATIONS[family]:
        raise ValueError("Unsupported mutation proposal")
    if variant and CATALOG[variant]["family"] != family:
        raise ValueError("Mutation operator belongs to another family")
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
            csv_text, expected = module.fixture(current_seed, level, variant=variant)
            _write(fixture_dir / "observations.csv", csv_text)
            case = dict(kind="etl", filename="observations.csv", output="summary.json", expected=expected)
        elif family == "repository-debug":
            value, expected = module.fixture(current_seed, level, variant=variant)
            _json(fixture_dir / "intervals.json", value)
            case = dict(kind="repository", filename="intervals.json", output="result.json", input=value,
                        expected=expected)
        else:
            expected = module.fixture(fixture_dir, current_seed, level, variant=variant)
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
    public_instruction = _public_instruction(module.INSTRUCTION)
    _write(task / "environment" / "public" / "README.md", public_instruction)
    _write(task / "instruction.md", public_instruction)
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
    if variant:
        negative = module.SOURCE
        replacements = {
            "etl-nullable-revisions": ("if row['status'] != 'ok':", "if False:"),
            "etl-mixed-unit-ordering": ("value -= 273.15", "value += 273.15"),
            "raster-grid-shift-nodata": ("valid_b = b != -9999", "valid_b = np.ones(b.shape, dtype=bool)"),
            "raster-non-square-spacing": ("resampling=Resampling.nearest", "resampling=Resampling.bilinear"),
            "repository-boundary-overlap": ("start <= merged[-1][1]", "start < merged[-1][1]"),
            "repository-input-ordering": ("sorted((a, b) for a, b in intervals if a < b)", "[(a, b) for a, b in intervals if a < b]"),
        }
        before, after = replacements[variant]
        if before not in negative:
            raise ValueError("Mutation negative control no longer matches its versioned source")
        _write(task / "controls" / "operator-negative.py", _installer(negative.replace(before, after), input_name))
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
    extra = ("RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 "
             "&& rm -rf /var/lib/apt/lists/*\n"
             "RUN python -m pip install --no-cache-dir numpy==2.2.6 rasterio==1.4.3\n"
             if family == "geospatial-raster" else "")
    # Harbor 0.13.2 builds a separate verifier from tests/; it never falls back
    # to the public agent image. Only trusted grading files enter this image.
    _write(task / "tests" / "Dockerfile", "FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c\n"
           + extra + "WORKDIR /app\nCOPY . /tests/\n"
           "RUN chmod -R go-rwx /tests && mkdir -p /logs/verifier && chmod 700 /logs/verifier\nUSER root\n")
    _write(task / "environment" / "Dockerfile", "FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c\n"
           "RUN apt-get update && apt-get install -y --no-install-recommends bash tmux asciinema && rm -rf /var/lib/apt/lists/*\n"
           "RUN asciinema --version\n"
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
    if variant:
        manifest["controls"]["mutations"].append("controls/operator-negative.py")
    def hashes(directory):
        return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*")) if path.is_file()}
    requirement_specs = {
        "scientific-etl": [
            ("ETL-R01", "Select the highest revision before finite-value and exact-ok status filtering", "verify: hidden fixture equality"),
            ("ETL-R02", "Convert C/F/K then aggregate by station with declared rounding and ordering", "verify: _equal summary.json"),
            ("ETL-R03", "Overwrite output idempotently under repeated calls", "verify: repeated _run and _equal"),
        ],
        "geospatial-raster": [
            ("RASTER-R01", "Preserve reference CRS, affine, shape, float32 and NoData metadata", "_check_raster: CRS/Affine/Grid/Band/dtype/NoData assertions"),
            ("RASTER-R02", "Sample nearest target centers and average valid values without treating NoData as zero", "_check_raster: NoData mask and pixel equality; _equal summary"),
            ("RASTER-R03", "Overwrite outputs idempotently", "verify: repeated _run and _check_raster"),
        ],
        "repository-debug": [
            ("REPO-R01", "Sorted disjoint union merges touching intervals and discards empties", "verify: CLI and function _equal against independently enumerated cells"),
            ("REPO-R02", "Reject reversed intervals, accept empty input and negative coordinates", "verify: dedicated reversed/empty/negative fixtures"),
            ("REPO-R03", "CLI/API agree, input is not mutated and output is idempotent", "verify: repeated _run and function.json equality"),
        ],
    }
    requirement_prefix = {"scientific-etl": "ETL", "geospatial-raster": "RASTER",
                          "repository-debug": "REPO"}[family]
    requirement_specs[family].append((
        requirement_prefix + "-R04",
        "Honor the public runtime and artifact limits, including file types, sizes, transfers and execution resources",
        "verify/_read_json/_check_raster file assertions; _run resource limits; hf-docker-v1 transfer/tmpfs enforcement",
    ))
    manifest.update(family_version="2", requirements=[dict(id=key, public_behavior=text, verifier_assertion=assertion)
                    for key, text, assertion in requirement_specs[family]],
                    artifact_contract={"source": "/app/pipeline.py", "source_max_bytes_exclusive": 1048576,
                                       "json_max_bytes_inclusive": 1048576, "raster_max_bytes_exclusive": 1048576,
                                       "regular_singly_linked_files_only": True,
                                       "output_directory": "/app/output", "declared_transfer_paths": manifest["public_artifacts"],
                                       "transfer_max_paths_inclusive": 128,
                                       "transfer_file_max_bytes_inclusive": 8 * 1024 * 1024,
                                       "transfer_total_max_bytes_inclusive": 16 * 1024 * 1024,
                                       "runtime_policy": "hf-docker-v1",
                                       "container": {"cpus": 1, "memory_bytes": 2 * 1024 ** 3, "pids": 64,
                                                     "app_bytes": 32 * 1024 ** 2, "tmp_bytes": 64 * 1024 ** 2,
                                                     "home_bytes": 32 * 1024 ** 2, "logs_bytes": 16 * 1024 ** 2,
                                                     "network": "none", "read_only_root": True},
                                       "protected_execution": {"wall_seconds": 20, "cpu_seconds": 15,
                                                               "address_space_bytes": 1024 ** 3,
                                                               "processes": 16, "file_descriptors": 64,
                                                               "file_write_bytes": 8 * 1024 ** 2}},
                    public_input_hashes=hashes(task / "environment" / "public" / "input"),
                    private_validation_hash=hashlib.sha256(json.dumps(hashes(task / "tests"), sort_keys=True).encode()).hexdigest(),
                    oracle_version="family-v2-independent-controls", quality_protocol_version="container-controls-v2",
                    difficulty_parameters={"level": level, "operator_id": variant, "bounds": {"level_min": 0, "level_max": 10}},
                    parent_version_id=manifest["parent_task_version_id"], mutation_id=operator_id,
                    generation_method="deterministic-family-renderer-v2",
                    source_provenance={"kind": "synthetic-generated-fixture", "seed": seed,
                                       "third_party_dataset": None, "license": "repository licensing; no additional grant"})
    _json(task / "manifest.json", manifest)
    manifest["artifact_sha256"] = task_digest(task)
    _json(task / "manifest.json", manifest)
    return task
