from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harborforge.authoring.provenance import task_digest
from harborforge.task_families.registry import generate_task
from harborforge.validation import validate_task


def test_clean_local_controls_reject_nop_wrong_and_shortcut(tmp_path):
    task = generate_task("scientific-etl", 123, tmp_path)
    report = validate_task(task, runtime="local", evidence_dir=tmp_path / "evidence")
    assert report["valid"], report
    assert not report["eligible_for_model_evaluation"]
    expected = {"oracle_1": 0, "oracle_2": 0, "alternate": 0, "nop": 1, "known_bad": 1, "negative_mutation_1": 1}
    gates = {gate["name"]: gate for gate in report["gates"]}
    for name, code in expected.items():
        assert gates[name]["verifier_exit_code"] == code
    assert json.loads(Path(report["evidence_path"]).read_text())["task_sha256"] == task_digest(task)


def test_modified_task_digest_blocks_controls(tmp_path):
    task = generate_task("scientific-etl", 123, tmp_path)
    (task / "tests" / "verify.py").write_text("raise SystemExit(0)\n")
    report = validate_task(task)
    assert not report["valid"]
    assert not report["eligible_for_model_evaluation"]
    assert report["status"] == "VALIDATION_ERROR"


def test_private_build_context_file_is_rejected(tmp_path):
    task = generate_task("scientific-etl", 123, tmp_path)
    (task / "environment" / "tests").mkdir()
    (task / "environment" / "tests" / "leak.py").write_text("expected = 42")
    manifest_path = task / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact_sha256"] = task_digest(task)
    manifest_path.write_text(json.dumps(manifest))
    report = validate_task(task)
    assert not report["valid"]
    assert not next(gate for gate in report["gates"] if gate["name"] == "leakage_static")["passed"]


def test_stopped_docker_is_validation_error_not_invalid_task(monkeypatch, tmp_path):
    task = generate_task("scientific-etl", 123, tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda command, **kw:
                        subprocess.CompletedProcess(command, 1, "", "cannot connect to Docker daemon"))
    report = validate_task(task, runtime="docker", evidence_dir=tmp_path / "evidence")
    assert not report["valid"]
    assert report["status"] == "VALIDATION_ERROR"
    assert not report["eligible_for_model_evaluation"]
