"""The public case study is derived from receipts and cannot export private trial material."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from harborforge.authoring.provenance import task_digest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def builder():
    spec = importlib.util.spec_from_file_location("case_study_builder", ROOT / "scripts/build_free_model_case_study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def receipt_root(tmp_path, builder):
    evidence = tmp_path / "source/docs/evidence"
    evidence.mkdir(parents=True)
    for name in builder.RECEIPTS:
        shutil.copyfile(ROOT / "docs/evidence" / name, evidence / name)
    return evidence.parents[1]


def modify(root, name, update):
    path = root / "docs/evidence" / name
    value = json.loads(path.read_text())
    update(value)
    path.write_text(json.dumps(value))


def test_aggregate_known_receipts_and_exclude_unreviewed_fields(builder, receipt_root):
    marker = "PRIVATE-TRACE-MUST-NOT-BE-EXPORTED"
    modify(receipt_root, builder.RECEIPTS[0], lambda v: v.update(private_trace=marker))
    result = builder.read_results(receipt_root)
    assert result["trial_count"] == 24
    assert result["outcomes"] == {"AGENT_TIMEOUT": 10, "PASS": 14}
    assert result["trial_provider_requests"] == 138
    assert result["availability_provider_requests"] == 4
    assert result["earlier_protocol_development_requests_excluded"] == 2
    assert result["cost_usd"] == result["new_model_calls"] == 0
    assert [m["passes"] for m in result["models"]] == [0, 6, 6, 2]
    assert len(result["sources"]) == 2
    assert all(len(source["sha256"]) == 64 for source in result["sources"])
    assert marker not in json.dumps(result)
    assert "source_sha256" not in json.dumps(result)


@pytest.mark.parametrize("field,value", [
    ("cost_usd", 0.1), ("cost_usd", None), ("cost_usd", False),
    ("all_provider_receipts_known_and_zero", False),
    ("observed_trials", 17), ("unrun_trials", 1), ("actual_provider_requests", 217),
    ("planned_trials", True), ("observed_trials", True), ("unrun_trials", False),
])
def test_refuse_incomplete_or_unknown_accounting(builder, receipt_root, field, value):
    modify(receipt_root, builder.RECEIPTS[0], lambda v: v.update({field: value}))
    with pytest.raises(ValueError):
        builder.read_results(receipt_root)


@pytest.mark.parametrize("change", [
    lambda v: v["model_results"][0]["outcomes"].update(AGENT_TIMEOUT=5),
    lambda v: v["model_results"][0].update(known_cost_usd=None),
    lambda v: v["model_results"][0].update(known_cost_usd=0.01),
    lambda v: v["model_results"][1].update(model_id=v["model_results"][0]["model_id"]),
])
def test_refuse_model_denominator_cost_or_identity_drift(builder, receipt_root, change):
    modify(receipt_root, builder.RECEIPTS[0], change)
    with pytest.raises(ValueError):
        builder.read_results(receipt_root)


@pytest.mark.parametrize("change", [
    lambda v: v.update(reference_campaign_id="another-campaign"),
    lambda v: v.update(exact_reference_tasks_reused=False),
    lambda v: v.update(harness_and_sampling_matched=False),
    lambda v: v["snapshots_sha256"].update({"harness-provenance.json": "0" * 64}),
])
def test_refuse_unmatched_flint_reference(builder, receipt_root, change):
    modify(receipt_root, builder.RECEIPTS[1], change)
    with pytest.raises(ValueError):
        builder.read_results(receipt_root)


def test_existing_output_is_preserved(builder, tmp_path):
    path = tmp_path / "existing"
    path.mkdir()
    marker = path / "keep.txt"
    marker.write_text("untouched")
    with pytest.raises(FileExistsError):
        builder.build(ROOT, path)
    assert marker.read_text() == "untouched"


def test_public_reference_replay_demonstrates_real_fix_and_control_status(builder, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "recording-only-private-canary")
    original_run = builder.subprocess.run
    def checked_run(*args, **kwargs):
        assert "OPENROUTER_API_KEY" not in kwargs["env"]
        return original_run(*args, **kwargs)
    monkeypatch.setattr(builder.subprocess, "run", checked_run)
    task = ROOT / "tasks/public_examples/repository-debug-100-v1"
    before_hash = task_digest(task)
    output = tmp_path / "report"
    report = builder.build(ROOT, output)
    summary = json.loads((output / "summary.json").read_text())
    before = json.loads((output / "samples/before.json").read_text())
    after = json.loads((output / "samples/after.json").read_text())
    assert before["covered"] == after["covered"] == 21
    assert before["intervals"] != after["intervals"]
    assert after["intervals"] == [[-11, -6], [-4, 3], [5, 14]]
    sample = summary["public_reference_sample"]
    assert sample["before_verifier_exit_code"] == 1 and sample["after_verifier_exit_code"] == 0
    assert sample["model_calls"] == 0
    assert sample["eligible_for_model_evaluation"] is False
    assert sample["container_isolation_verified"] is False
    gates = {gate["name"]: gate["passed"] for gate in sample["controls"]}
    assert all(gates[name] for name in ("oracle_1", "oracle_2", "nop", "known_bad", "alternate", "negative_mutation_1"))
    assert task_digest(task) == before_hash
    assert "-        if merged and start < merged[-1][1]:" in (output / "samples/pipeline.patch").read_text()
    assert "+        if merged and start <= merged[-1][1]:" in (output / "samples/pipeline.patch").read_text()
    markup = report.read_text()
    assert "no model call" in markup and "author/reference code locally" in markup
    assert "138" in markup and "24" in markup
    assert "    [-11, -6]" in markup and "    [-4, 3]" in markup
    assert markup.count("&quot;covered&quot;: 21") == 2
    assert "The public sample is an author/reference replay" in markup
    assert "[\n      -11," in (output / "samples/after.json").read_text()
    assert "<script" not in markup and "https://" not in markup and "file://" not in markup
    assert len((output / "model-results.csv").read_text().splitlines()) == 5
    assert not (output / "local-controls").exists()
