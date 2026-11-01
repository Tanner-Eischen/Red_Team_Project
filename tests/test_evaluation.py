import json

import pytest

from harborforge.evaluation import freeze_suite, preregistration_config, verify_suite

from test_registry import make_task


def preregistration():
    return {"campaign_id": "fixed-evaluation", "protocol": "terminal_agent", "objective": "fixed_evaluation",
            "cohort": "holdout", "model_profiles": [{"profile_id": "a", "model_id": "known", "repeats": 3}],
            "harness_profile": {"profile_id": "harbor-fixed", "timeout_seconds": 600},
            "include_timeouts_in_denominator": True, "budgets": {"max_task_revisions": 0},
            "suite_manifest": "ignored.json", "tasks": ["also-bound-by-suite"]}


def test_freeze_preserves_preregistration_and_refuses_overwrite_and_drift(tmp_path):
    path, _ = make_task(tmp_path, cohort="holdout")
    output = tmp_path / "suite.json"
    suite = freeze_suite([path], output, preregistration())
    assert suite["cohort"] == "holdout"
    assert suite["preregistration"] == preregistration_config(preregistration())
    assert verify_suite(output, preregistration()) == suite
    with pytest.raises(FileExistsError):
        freeze_suite([path], output, preregistration())
    changed = preregistration()
    changed["model_profiles"][0]["model_id"] = "different"
    with pytest.raises(ValueError, match="preregistration"):
        verify_suite(output, changed)
    (path / "instruction.md").write_text("Changed", encoding="utf-8")
    with pytest.raises(ValueError, match="drift"):
        verify_suite(output)


def test_suite_tampering_and_duplicate_tasks_are_rejected(tmp_path):
    path, _ = make_task(tmp_path, cohort="holdout")
    output = tmp_path / "suite.json"
    with pytest.raises(ValueError, match="duplicate"):
        freeze_suite([path, path], output, preregistration())
    freeze_suite([path], output, preregistration())
    data = json.loads(output.read_text(encoding="utf-8"))
    data["preregistration"]["include_timeouts_in_denominator"] = False
    output.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_suite(output)


def test_freeze_requires_complete_experiment_and_single_cohort(tmp_path):
    path, _ = make_task(tmp_path, cohort="holdout")
    other, _ = make_task(tmp_path, task_id="other", cohort="discovery")
    with pytest.raises(ValueError, match="mix"):
        freeze_suite([path, other], tmp_path / "mixed.json", preregistration())
    config = preregistration()
    config["model_profiles"][0]["repeats"] = 0
    with pytest.raises(ValueError, match="positive repeats"):
        freeze_suite([path], tmp_path / "bad.json", config)
    config = preregistration()
    config.pop("include_timeouts_in_denominator")
    with pytest.raises(ValueError, match="Timeout"):
        freeze_suite([path], tmp_path / "bad.json", config)
    with pytest.raises(ValueError, match="outside"):
        freeze_suite([path], path / "suite.json", preregistration())
