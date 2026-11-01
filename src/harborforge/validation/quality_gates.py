"""Reproducible task controls; local functional checks are never Docker approval."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

from harborforge.adapters.harbor_adapter import HarborAdapter, redact


def _gate(name: str, passed: bool, **evidence: Any) -> dict:
    return {"name": name, "passed": passed, **evidence}


def _structure(task: Path) -> tuple[dict, list[dict]]:
    from harborforge.authoring.provenance import read_manifest

    gates = []
    manifest = read_manifest(task, verify=True)
    required = ["instruction.md", "task.toml", "environment/Dockerfile", "solution/solve.py",
                "solution/solve.sh", "tests/verify.py", "tests/test.sh", "tests/Dockerfile",
                "controls/alternate.py", "controls/bad.py"]
    missing = [name for name in required if not (task / name).is_file()]
    gates.append(_gate("structure", not missing, missing=missing))
    config = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
    allowed_sections = {"schema_version", "metadata", "artifacts", "agent", "verifier", "environment"}
    allowed_fields = {
        "agent": {"timeout_sec", "user", "network_mode"},
        "verifier": {"timeout_sec", "user", "environment_mode", "network_mode"},
        "environment": {"build_timeout_sec", "cpus", "memory_mb", "storage_mb", "network_mode"},
    }
    unknown = sorted(set(config) - allowed_sections)
    for section, fields in allowed_fields.items():
        unknown.extend(f"{section}.{key}" for key in set(config.get(section, {})) - fields)
    gates.append(_gate("pinned_schema", not unknown, unsupported_fields=unknown))
    artifacts = config.get("artifacts", [])
    artifact_paths = [item if isinstance(item, str) else item.get("source") for item in artifacts]
    declared = manifest.get("public_artifacts", [])
    gates.append(_gate("harbor_contract", config.get("schema_version") == "1.3"
                       and config.get("verifier", {}).get("environment_mode") == "separate"
                       and config.get("environment", {}).get("network_mode") == "no-network"
                       and config.get("verifier", {}).get("network_mode") == "no-network"
                       and config.get("agent", {}).get("user") == "agent"
                       and config.get("verifier", {}).get("user") == "root"
                       and bool(declared) and sorted(artifact_paths) == sorted(declared),
                       schema_version=config.get("schema_version"), artifacts=artifact_paths))
    if missing:
        return manifest, gates
    dockerfile = (task / "environment" / "Dockerfile").read_text(encoding="utf-8")
    private_names = {"solution", "tests", "controls", "private", "expected.json"}
    leaked = [p.relative_to(task / "environment").as_posix() for p in (task / "environment").rglob("*")
              if p.is_symlink() or any(part in private_names for part in p.relative_to(task / "environment").parts)]
    bad_copy = []
    for line in dockerfile.splitlines():
        if re.match(r"\s*(ADD|COPY)\s", line, re.IGNORECASE):
            if not re.match(r"\s*COPY\s+(?:--chown=\S+\s+)?public/\s+/app/?\s*$", line):
                bad_copy.append(line)
    verifier = (task / "tests" / "test.sh").read_text(encoding="utf-8")
    gates.append(_gate("leakage_static", not leaked and not bad_copy
                       and not (task / "environment" / "docker-compose.yaml").exists()
                       and not (task / "tests" / "docker-compose.yaml").exists()
                       and "/solution/" not in verifier and "--oracle" not in verifier,
                       leaked_paths=leaked, unsupported_copies=bad_copy,
                       limitation="Static source check; Docker image inspection is required for production approval"))
    return manifest, gates


def _control_paths(manifest: dict) -> list[tuple[str, str | None, bool]]:
    controls = manifest.get("controls", {})
    result = [("oracle_1", controls.get("oracle", "solution/solve.py"), True),
              ("oracle_2", controls.get("oracle", "solution/solve.py"), True),
              ("nop", None, False),
              ("known_bad", controls.get("bad", "controls/bad.py"), False),
              ("alternate", controls.get("alternate", "controls/alternate.py"), True)]
    for index, path in enumerate(controls.get("mutations", [])):
        result.append((f"negative_mutation_{index + 1}", path, False))
    return result


def _safe_script(task: Path, relative: str) -> Path:
    path = (task / relative).resolve()
    if not path.is_relative_to(task.resolve()) or not path.is_file():
        raise ValueError(f"Invalid control script path: {relative}")
    return path


def _remaining(deadline: float, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("task validation", 0)
    return min(cap, remaining)


def _local_controls(task: Path, manifest: dict, deadline: float) -> list[dict]:
    gates = []
    # Generated deterministic control programs are author code. This mode is a
    # local contract test, not a sandbox for arbitrary model-produced programs.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "WINDIR", "LANG", "LC_ALL"}}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    for name, script, expected_pass in _control_paths(manifest):
        with tempfile.TemporaryDirectory(prefix="redforge-control-") as directory:
            workspace = Path(directory) / "workspace"
            shutil.copytree(task / "environment" / "public", workspace)
            solver_code = 0
            solver_output = ""
            try:
                if script is not None:
                    solver = subprocess.run([sys.executable, str(_safe_script(task, script)), "--workspace", str(workspace)],
                                            capture_output=True, text=True, timeout=_remaining(deadline, 30), env=environment)
                    solver_code, solver_output = solver.returncode, solver.stdout + solver.stderr
                verifier = subprocess.run([sys.executable, str(task / "tests" / "verify.py"), "--workspace", str(workspace)],
                                          capture_output=True, text=True, timeout=_remaining(deadline, 60), env=environment)
                expected_code = 0 if expected_pass else 1
                gates.append(_gate(name, solver_code == 0 and verifier.returncode == expected_code,
                                   solver_exit_code=solver_code, verifier_exit_code=verifier.returncode,
                                   expected_verifier_exit_code=expected_code,
                                   output=redact((solver_output + verifier.stdout + verifier.stderr)[-16000:])))
            except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
                gates.append(_gate(name, False, error=redact(str(exc)), status="VALIDATION_ERROR"))
    return gates


def _docker_controls(task: Path, manifest: dict, evidence: Path, deadline: float) -> list[dict]:
    gates: list[dict] = []
    tag = f"redforge-gate-{manifest['artifact_sha256'][:16]}"
    build = subprocess.run(["docker", "build", "--pull=false", "--tag", tag, str(task / "environment")],
                           capture_output=True, text=True, timeout=_remaining(deadline, 600))
    (evidence / "build.log").write_text(redact(build.stdout + build.stderr), encoding="utf-8")
    gates.append(_gate("build", build.returncode == 0, evidence=str(evidence / "build.log"),
                       status="VALIDATED" if build.returncode == 0 else "VALIDATION_ERROR"))
    if build.returncode:
        return gates
    inspect = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                             capture_output=True, text=True, timeout=_remaining(deadline, 20))
    isolation = subprocess.run(["docker", "run", "--rm", "--network=none", "--cpus=1", "--memory=512m", tag,
                                "/bin/sh", "-c", "test ! -e /solution && test ! -e /tests && test ! -e /controls && test $(id -u) -ne 0"],
                               capture_output=True, text=True, timeout=_remaining(deadline, 30))
    gates.append(_gate("agent_image_isolation", isolation.returncode == 0 and inspect.returncode == 0,
                       image_digest=inspect.stdout.strip(), output=redact(isolation.stdout + isolation.stderr),
                       status="VALIDATION_ERROR" if inspect.returncode else ("VALIDATED" if isolation.returncode == 0 else "INVALID_TASK")))
    if not gates[-1]["passed"]:
        return gates
    adapter = HarborAdapter()
    for name, script, expected_pass in _control_paths(manifest):
        with tempfile.TemporaryDirectory(prefix="redforge-harbor-control-") as directory:
            control_task = Path(directory) / task.name
            shutil.copytree(task, control_task)
            if script is not None and name not in {"oracle_1", "oracle_2"}:
                # Oracle agent uploads only this control's implementation. The
                # grader remains unchanged and never invokes a reference solver.
                shutil.copyfile(_safe_script(task, script), control_task / "solution" / "solve.py")
            result = adapter.run(control_task, {}, {"agent": "nop" if script is None else "oracle",
                                                    "trial_time_limit_sec": _remaining(deadline, 1200)}, evidence / name)
            expected = "PASS" if expected_pass else "MODEL_FAIL"
            gates.append(_gate(name, result["outcome"] == expected, expected_outcome=expected, result=result,
                               status="VALIDATION_ERROR" if result["outcome"] in
                               {"INFRA_ERROR", "PROVIDER_ERROR", "UNSCORABLE"} else "CONTROL_RESULT"))
    return gates


def validate_task(task_path: Path, *, runtime: str = "local", evidence_dir: Path | None = None,
                  timeout_seconds: float = 1200) -> dict:
    """Run controls from fresh workspaces and persist the exact evidence scope."""
    if runtime not in {"local", "docker"}:
        raise ValueError("runtime must be local or docker")
    task = Path(task_path).resolve()
    started = time.monotonic()
    report: dict[str, Any] = {"valid": False, "runtime": runtime, "task_sha256": None,
                              "eligible_for_model_evaluation": False, "gates": [],
                              "review_status": "pending", "status": "INVALID_TASK"}
    temporary = tempfile.TemporaryDirectory(prefix="redforge-validation-") if evidence_dir is None else None
    evidence = Path(temporary.name if temporary else evidence_dir).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        manifest, gates = _structure(task)
        report.update(task_sha256=manifest["artifact_sha256"], gates=gates)
        if all(gate["passed"] for gate in gates):
            if runtime == "local":
                gates.extend(_local_controls(task, manifest, started + timeout_seconds))
            else:
                gates.extend(_docker_controls(task, manifest, evidence, started + timeout_seconds))
        report["valid"] = bool(gates) and all(gate["passed"] for gate in gates)
        report["eligible_for_model_evaluation"] = report["valid"] and runtime == "docker"
        report["status"] = "VALIDATED" if report["valid"] else "INVALID_TASK"
        if not report["valid"] and any(gate.get("status") == "VALIDATION_ERROR" for gate in gates):
            report["status"] = "VALIDATION_ERROR"
        from harborforge.authoring.provenance import task_digest

        if task_digest(task) != manifest["artifact_sha256"]:
            gates.append(_gate("task_integrity_after_validation", False, status="INVALID_TASK"))
            report.update(valid=False, eligible_for_model_evaluation=False, status="INVALID_TASK")
        if runtime == "local":
            report["limitation"] = "Local functional controls only; no container, privilege or network isolation was tested."
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        report["gates"].append(_gate("validation_error", False, error=redact(str(exc))))
        report["status"] = "VALIDATION_ERROR"
    report["wall_time_s"] = time.monotonic() - started
    report_path = evidence / "validation.json"
    report["evidence_path"] = str(report_path) if not temporary else None
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if temporary:
        temporary.cleanup()
    return report
