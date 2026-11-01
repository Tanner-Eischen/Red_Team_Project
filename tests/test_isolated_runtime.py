from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess

import pytest

pytest.importorskip("harbor")
from harborforge.adapters.isolated_docker import MAX_FILES, MAX_FILE_BYTES, _host_tree, materialize
from harborforge.adapters.harbor_adapter import HarborAdapter
from harborforge.authoring.provenance import task_digest
from harborforge.task_families.registry import generate_task
from harborforge.validation.quality_gates import validate_task


def payload(name="result.json", data=b"{}"):
    return {"path": name, "data": base64.b64encode(data).decode()}


def test_bounded_binary_transfer_roundtrip(tmp_path):
    records = [{"path": "", "directory": True}, {"path": "nested", "directory": True},
               payload("nested/value.bin", b"\x00\xff\r\n")]
    hashes = materialize(records, tmp_path / "output", directory=True)
    assert (tmp_path / "output/nested/value.bin").read_bytes() == b"\x00\xff\r\n"
    assert len(hashes["nested/value.bin"]) == 64


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/outside", "C:relative", "a\\b", "a/../b", "a/./b", "a//b"])
def test_transfer_rejects_noncanonical_or_traversing_names_before_writes(tmp_path, name):
    with pytest.raises(ValueError):
        materialize([payload("good"), payload(name)], tmp_path / "output", directory=True)
    assert not (tmp_path / "output").exists()


def test_transfer_refuses_symlinked_parent_and_hardlinked_destination(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(ValueError):
        materialize([payload()], linked, directory=True)
    old = outside / "file"
    old.write_text("preserve")
    destination = tmp_path / "hardlinked"
    os.link(old, destination)
    with pytest.raises(ValueError):
        materialize([payload("", b"overwrite")], destination, directory=False)
    assert old.read_text() == "preserve"


def test_source_hardlinks_and_special_files_reject(tmp_path):
    original = tmp_path / "original"
    original.write_text("source")
    os.link(original, tmp_path / "alias")
    with pytest.raises(ValueError, match="Linked"):
        _host_tree(tmp_path)


def test_transfer_rejects_file_and_path_count_limits(tmp_path):
    with pytest.raises(ValueError, match="Oversized"):
        materialize([payload("", b"x" * (MAX_FILE_BYTES + 1))], tmp_path / "file", directory=False)
    with pytest.raises(ValueError, match="manifest"):
        materialize([payload(str(i)) for i in range(MAX_FILES + 1)], tmp_path / "output", directory=True)
    assert not (tmp_path / "file").exists()


def test_ignored_task_schema_field_rejects_before_runtime(tmp_path):
    task = generate_task("scientific-etl", 550, tmp_path)
    config = task / "task.toml"
    config.write_text(config.read_text() + "\nunsupported_security_field = true\n")
    manifest = task / "manifest.json"
    value = json.loads(manifest.read_text())
    value["artifact_sha256"] = task_digest(task)
    manifest.write_text(json.dumps(value))
    result = validate_task(task)
    assert not result["valid"]
    assert not next(gate for gate in result["gates"] if gate["name"] == "pinned_schema")["passed"]


def test_scored_harbor_reward_without_actual_runtime_evidence_is_unscorable(monkeypatch, tmp_path):
    def fake(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "0.13.2", "")
        config = json.loads(Path(command[-1]).read_text())
        trial = Path(config["jobs_dir"]) / "trial" / "fake"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps({"trial_name": "fake", "task_name": "fake",
            "finished_at": "2026-11-01", "verifier_result": {"rewards": {"reward": 1}}}))
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake)
    result = HarborAdapter().run(tmp_path, {}, {"agent": "nop"}, tmp_path / "trial")
    assert result["outcome"] == "UNSCORABLE" and result["reward"] is None
    assert result["error_code"] == "MISSING_RUNTIME_ISOLATION_EVIDENCE"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process cleanup contract")
def test_local_root_even_in_a_dev_container_never_scans_unmarked_candidate_uids(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from harborforge.task_families import verifier_template as verifier
    monkeypatch.setattr(verifier, "PROTECTED_POLICY", tmp_path / "absent-policy")
    monkeypatch.setattr(verifier.os, "geteuid", lambda: 0)
    groups = []
    monkeypatch.setattr(verifier.os, "killpg", lambda pid, signal: groups.append(pid))
    def forbid_scan(pid, signal):
        raise AssertionError("A local author control scanned unrelated host UIDs")
    monkeypatch.setattr(verifier.os, "kill", forbid_scan)
    verifier._stop_descendants(SimpleNamespace(pid=123456))
    assert groups == [123456]


def test_policy_marker_must_have_exact_private_runtime_contract(monkeypatch, tmp_path):
    from harborforge.task_families import verifier_template as verifier
    policy = tmp_path / "policy"
    monkeypatch.setattr(verifier, "PROTECTED_POLICY", policy)
    assert not verifier._protected_namespace()
    policy.write_text('{"policy_version":"hf-docker-v1","private_pid_namespace":false}')
    policy.chmod(0o400)
    assert not verifier._protected_namespace()
    policy.chmod(0o600)
    policy.write_text('{"policy_version":"hf-docker-v1","private_pid_namespace":true}')
    policy.chmod(0o644)
    assert not verifier._protected_namespace()
