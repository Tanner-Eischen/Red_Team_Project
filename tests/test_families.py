from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from harborforge.authoring.mutations import revise_task
from harborforge.authoring.provenance import read_manifest, task_digest
from harborforge.task_families import FAMILIES, generate_task


def _attempt(task: Path, script: str | None, destination: Path):
    shutil.copytree(task / "environment" / "public", destination)
    if script:
        result = subprocess.run([sys.executable, str(task / script), "--workspace", str(destination)],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
    return subprocess.run([sys.executable, str(task / "tests" / "verify.py"), "--workspace", str(destination)],
                          capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("family", FAMILIES)
def test_family_controls_independent_hidden_cases(tmp_path, family):
    if family == "geospatial-raster":
        pytest.importorskip("rasterio")
    task = generate_task(family, 123, tmp_path / "tasks")
    original_digest = task_digest(task)
    for index, script in enumerate(("solution/solve.py", "controls/alternate.py", "controls/bad.py",
                                    "controls/hardcoded.py", None)):
        result = _attempt(task, script, tmp_path / f"attempt-{index}")
        assert result.returncode == (0 if index < 2 else 1), (script, result.stderr)
    assert task_digest(task) == original_digest


@pytest.mark.parametrize("family", FAMILIES)
def test_generation_reproducible_and_immutable(tmp_path, family):
    if family == "geospatial-raster":
        pytest.importorskip("rasterio")
    first = generate_task(family, 95, tmp_path / "first")
    second = generate_task(family, 95, tmp_path / "second")
    different = generate_task(family, 96, tmp_path / "third")
    assert read_manifest(first)["artifact_sha256"] == read_manifest(second)["artifact_sha256"]
    assert task_digest(first) != task_digest(different)
    with pytest.raises(FileExistsError):
        generate_task(family, 95, tmp_path / "first")
    assert not any(path.name in ("solve.py", "verify.py", "cases.json")
                   for path in (first / "environment").rglob("*"))
    (first / "instruction.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        read_manifest(first)


def test_unknown_family_fails_closed_without_output(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        generate_task("echo-placeholder", 1, tmp_path / "tasks")
    assert not (tmp_path / "tasks").exists()


@pytest.mark.parametrize("family", FAMILIES)
def test_revision_changes_cases_and_preserves_lineage(tmp_path, family):
    if family == "geospatial-raster":
        pytest.importorskip("rasterio")
    task = generate_task(family, 321, tmp_path / "tasks")
    before = read_manifest(task)
    child = revise_task(task, tmp_path / "tasks", max_iterations=1)
    after = read_manifest(child)
    assert after["parent_task_version_id"] == before["task_version_id"]
    assert after["version"] == 2
    assert after["mutation"]["parent_artifact_sha256"] == before["artifact_sha256"]
    assert (task / "tests/cases.json").read_bytes() != (child / "tests/cases.json").read_bytes()
    assert read_manifest(task) == before
    with pytest.raises(ValueError, match="revision limit"):
        revise_task(child, tmp_path / "tasks", max_iterations=1)
    result = _attempt(child, "solution/solve.py", tmp_path / "child-attempt")
    assert result.returncode == 0, result.stderr


def test_holdout_cannot_be_revised(tmp_path):
    task = generate_task("scientific-etl", 1, tmp_path, cohort="holdout")
    with pytest.raises(ValueError, match="adaptive"):
        revise_task(task, tmp_path)
    with pytest.raises(ValueError, match="discovery"):
        generate_task("scientific-etl", 1, tmp_path / "bypass", version=2, parent=read_manifest(task))


def test_digest_covers_manifest_and_extra_files(tmp_path):
    task = generate_task("repository-debug", 1, tmp_path)
    manifest = read_manifest(task)
    original_digest = manifest["artifact_sha256"]
    manifest["artifact_sha256"] = "ignored-self-field"
    (task / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert task_digest(task) == original_digest
    # The self hash is omitted but all other manifest metadata remains covered.
    digest = task_digest(task)
    manifest["cohort"] = "calibration"
    (task / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert task_digest(task) != digest
    digest = task_digest(task)
    (task / "new-file").write_bytes(b"artifact")
    assert task_digest(task) != digest


@pytest.mark.parametrize("failure", ["missing-output", "invalid-json", "nonfinite", "boolean-count"])
def test_verifier_rejects_invalid_public_artifact(tmp_path, failure):
    task = generate_task("scientific-etl", 123, tmp_path / "tasks")
    workspace = tmp_path / "submission"
    result = _attempt(task, "solution/solve.py", workspace)
    assert result.returncode == 0, result.stderr
    output = workspace / "output" / "summary.json"
    if failure == "missing-output":
        shutil.rmtree(workspace / "output")
    elif failure == "invalid-json":
        output.write_text("{broken", encoding="utf-8")
    elif failure == "nonfinite":
        output.write_text('[{"station_id":"alpha","count":1,"mean_c":NaN}]', encoding="utf-8")
    else:
        summary = json.loads(output.read_text(encoding="utf-8"))
        summary[0]["count"] = True
        output.write_text(json.dumps(summary), encoding="utf-8")
    rejected = subprocess.run([sys.executable, str(task / "tests/verify.py"), "--workspace", str(workspace)],
                              capture_output=True, text=True, timeout=30)
    assert rejected.returncode == 1, rejected.stderr


def test_verifier_rejects_symlink_submission(tmp_path):
    task = generate_task("scientific-etl", 123, tmp_path / "tasks")
    workspace = tmp_path / "submission"
    shutil.copytree(task / "environment/public", workspace)
    candidate = workspace / "pipeline.py"
    candidate.unlink()
    try:
        candidate.symlink_to(task / "environment/public/pipeline.py")
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    rejected = subprocess.run([sys.executable, str(task / "tests/verify.py"), "--workspace", str(workspace)],
                              capture_output=True, text=True, timeout=30)
    assert rejected.returncode == 1, rejected.stderr


def test_early_exit_cannot_bypass_importable_function_checks(tmp_path):
    from harborforge.task_families.repository_debug import SOURCE
    task = generate_task("repository-debug", 123, tmp_path / "tasks")
    workspace = tmp_path / "submission"
    result = _attempt(task, "solution/solve.py", workspace)
    assert result.returncode == 0, result.stderr
    candidate = SOURCE.replace("import argparse", "import argparse\nimport os\nif __name__ != '__main__': os._exit(0)")
    (workspace / "pipeline.py").write_text(candidate, encoding="utf-8")
    rejected = subprocess.run([sys.executable, str(task / "tests/verify.py"), "--workspace", str(workspace)],
                              capture_output=True, text=True, timeout=60)
    assert rejected.returncode == 1, rejected.stderr
