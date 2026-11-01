"""Real local author controls; never certify container isolation/model behavior."""
import json
import shutil
import subprocess
import sys

import pytest
from pydantic import ValidationError

from harborforge.authoring.mutations import CATALOG, revise_task
from harborforge.authoring.proposals import MutationProposal
from harborforge.authoring.provenance import read_manifest, task_digest
from harborforge.task_families.registry import generate_task


def control(task, script, workspace):
    shutil.copytree(task / "environment/public", workspace)
    run = subprocess.run([sys.executable, str(task / script), "--workspace", str(workspace)],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return subprocess.run([sys.executable, str(task / "tests/verify.py"), "--workspace", str(workspace)],
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("operator", list(CATALOG))
def test_each_catalog_operator_changes_real_inputs_and_passes_independent_controls(tmp_path, operator):
    specification = CATALOG[operator]
    if specification["family"] == "geospatial-raster":
        pytest.importorskip("rasterio")
    parent = generate_task(specification["family"], 123, tmp_path / "tasks")
    original = task_digest(parent)
    child = revise_task(parent, tmp_path / "children", mutation=operator, max_iterations=1)
    manifest = read_manifest(child)
    assert manifest["parent_version_id"] == read_manifest(parent)["task_version_id"]
    assert manifest["mutation"]["operator"] == operator
    assert manifest["mutation"]["difficulty_measured"] is False
    assert manifest["public_input_hashes"] != read_manifest(parent)["public_input_hashes"]
    assert (child / "tests/cases.json").read_bytes() != (parent / "tests/cases.json").read_bytes()
    assert "controls/operator-negative.py" in manifest["controls"]["mutations"]
    assert all(item["id"] and item["public_behavior"] and item["verifier_assertion"] for item in manifest["requirements"])
    for index, script in enumerate(["solution/solve.py", "controls/alternate.py", "controls/operator-negative.py"]):
        result = control(child, script, tmp_path / f"control-{index}")
        assert result.returncode == (0 if index < 2 else 1), (script, result.stderr)
    assert task_digest(parent) == original


def test_variant_dimension_changes_are_not_just_catalog_labels(tmp_path):
    parent = generate_task("scientific-etl", 123, tmp_path / "tasks")
    nullable = revise_task(parent, tmp_path / "nullable", mutation="etl-nullable-revisions")
    mixed = revise_task(parent, tmp_path / "mixed", mutation="etl-mixed-unit-ordering")
    assert (nullable / "tests/cases.json").read_bytes() != (mixed / "tests/cases.json").read_bytes()
    assert (nullable / "environment/public/input/observations.csv").read_bytes() != (mixed / "environment/public/input/observations.csv").read_bytes()


@pytest.mark.parametrize("extra", [{"shell": "echo forbidden"}, {"dependencies": ["unreviewed"]}, {"level": 11}])
def test_structured_author_proposals_reject_unbounded_or_executable_fields(extra):
    proposal = dict(operator_id="etl-mixed-unit-ordering", family="scientific-etl", level=1, mode="all_pass")
    with pytest.raises(ValidationError):
        MutationProposal.model_validate({**proposal, **extra})


def test_cross_family_and_unknown_proposals_fail_before_creation(tmp_path):
    parent = generate_task("repository-debug", 123, tmp_path / "tasks")
    with pytest.raises(ValueError, match="another task family"):
        revise_task(parent, tmp_path / "bad", mutation="etl-nullable-revisions")
    assert not (tmp_path / "bad").exists()
    with pytest.raises(ValueError, match="Unsupported mutation"):
        generate_task("repository-debug", 124, tmp_path / "bad", mutation={"operator": "run-shell"})
    assert not (tmp_path / "bad").exists()


def test_family_version_separates_new_roots_from_published_ids(tmp_path):
    task = generate_task("repository-debug", 123, tmp_path / "tasks")
    manifest = json.loads((task / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["task_id"] == "repository-debug-f2-123" and manifest["family_version"] == "2"
    assert manifest["generation_method"] == "deterministic-family-renderer-v2"
    assert (task / "tests/Dockerfile").is_file()
    assert "COPY . /tests/" in (task / "tests/Dockerfile").read_text()


@pytest.mark.parametrize("operator", ["raster-grid-shift-nodata", "raster-non-square-spacing"])
def test_saturated_raster_operator_rejects_third_metadata_only_child(tmp_path, operator):
    pytest.importorskip("rasterio")
    from harborforge.authoring.mutations import NoSemanticChange

    parent = generate_task("geospatial-raster", 123, tmp_path / "tasks")
    original = task_digest(parent)
    first = revise_task(parent, tmp_path / "children", mutation=operator, max_iterations=10)
    second = revise_task(first, tmp_path / "children", mutation=operator, max_iterations=10)
    before = task_digest(second)
    expected_third = tmp_path / "children" / f"{read_manifest(parent)['task_id']}-v4"
    with pytest.raises(NoSemanticChange, match="public input and private case bytes are unchanged"):
        revise_task(second, tmp_path / "children", mutation=operator, max_iterations=10)
    assert not expected_third.exists() and len(list((tmp_path / "children").iterdir())) == 2
    assert task_digest(parent) == original and task_digest(second) == before
    other = "raster-non-square-spacing" if operator == "raster-grid-shift-nodata" else "raster-grid-shift-nodata"
    switched = revise_task(second, tmp_path / "children", mutation=other, max_iterations=10)
    assert read_manifest(switched)["version"] == 4
    assert read_manifest(switched)["public_input_hashes"] != read_manifest(second)["public_input_hashes"]


def test_revision_cannot_mutate_parent_by_nesting_output_inside_it(tmp_path):
    parent = generate_task("repository-debug", 123, tmp_path / "tasks")
    before = task_digest(parent)
    with pytest.raises(ValueError, match="inside its immutable parent"):
        revise_task(parent, parent / "nested", mutation="repository-boundary-overlap")
    assert not (parent / "nested").exists() and task_digest(parent) == before
