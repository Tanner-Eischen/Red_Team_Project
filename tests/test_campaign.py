"""Controller contract tests. Injected trial results are not model measurements."""

import pytest

from harborforge.authoring.provenance import read_manifest
from harborforge.config import CampaignConfig
from harborforge.execution.campaign import run_campaign
from harborforge.storage.registry import Registry
from harborforge.task_families.registry import generate_task


def configuration(task, **overrides):
    value = dict(campaign_id="contract-test", tasks=[str(task)],
        harness_profile=dict(timeout_seconds=1, verifier_timeout_seconds=1, trial_time_limit_sec=5,
                             max_input_tokens=100, max_output_tokens=50),
        budgets=dict(campaign_usd_max=5, total_input_tokens_max=500, total_output_tokens_max=250,
                     max_model_attempts=5, wall_time_seconds_max=120),
        model_profiles=[dict(profile_id="test-model", provider="unit-test", model_id="unit-test/fake",
            family="test", max_cost_usd_per_attempt=1, max_input_tokens=100, max_output_tokens=50,
            input_usd_per_million=2, output_usd_per_million=4,
            price_source="test fixture", price_checked_utc="2026-10-31T00:00:00Z")])
    value.update(overrides)
    return CampaignConfig.model_validate(value)


def validation(path, **kwargs):
    return dict(valid=True, runtime="docker", eligible_for_model_evaluation=True,
                task_sha256=read_manifest(path)["artifact_sha256"], status="VALIDATED", gates=[])


class FakeAdapter:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def run(self, task, model, harness, output_dir):
        self.calls.append(str(task))
        outcome = next(self.outcomes)
        return dict(outcome=outcome, reward=1 if outcome == "PASS" else 0 if outcome == "MODEL_FAIL" else None,
                    cost_usd=0.01, tokens_input=20, tokens_output=10, wall_time_s=0.1)


def test_every_attempt_including_retry_is_recorded(tmp_path):
    task = generate_task("scientific-etl", 13, tmp_path / "tasks")
    config = configuration(task, budgets=dict(campaign_usd_max=5, total_input_tokens_max=500,
        total_output_tokens_max=250, max_model_attempts=5, wall_time_seconds_max=120, retry_provider_errors=1))
    adapter = FakeAdapter(["PROVIDER_ERROR", "PASS"])
    summary = run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter, validator=validation)
    with Registry(tmp_path / "registry.sqlite") as registry:
        trials = registry.list_trials(config.campaign_id)
        assert [t["outcome"] for t in trials] == ["PROVIDER_ERROR", "PASS"]
        assert [t["attempt_number"] for t in trials] == [0, 1]
        assert all(registry.list_events(t["trial_id"]) for t in trials)
    assert summary["budget"]["attempts"] == 2


def test_failed_quality_never_calls_adapter(tmp_path):
    task = generate_task("scientific-etl", 14, tmp_path / "tasks")
    adapter = FakeAdapter([])
    run_campaign(configuration(task), tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter,
                 validator=lambda path, **kw: {**validation(path), "valid": False})
    with Registry(tmp_path / "registry.sqlite") as registry:
        assert registry.list_trials("contract-test")[0]["outcome"] == "INVALID_TASK"
    assert adapter.calls == []


def test_local_validation_cannot_certify_real_evaluation(tmp_path):
    task = generate_task("repository-debug", 15, tmp_path / "tasks")
    adapter = FakeAdapter([])
    run_campaign(configuration(task), tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter,
                 validator=lambda path, **kw: {**validation(path), "runtime": "local"})
    assert adapter.calls == []


def test_unknown_usage_blocks_retry(tmp_path):
    task = generate_task("repository-debug", 16, tmp_path / "tasks")

    class Unknown(FakeAdapter):
        def run(self, *args):
            return {**super().run(*args), "cost_usd": None}

    adapter = Unknown(["PROVIDER_ERROR", "PASS"])
    summary = run_campaign(configuration(task), tmp_path / "registry.sqlite", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert len(adapter.calls) == 1
    assert summary["status"] == "usage_reconciliation_required"


def test_failure_driven_child_is_bounded_and_revalidated(tmp_path):
    task = generate_task("scientific-etl", 17, tmp_path / "tasks")
    config = configuration(task, budgets=dict(campaign_usd_max=5, total_input_tokens_max=500,
        total_output_tokens_max=250, max_model_attempts=5, wall_time_seconds_max=120, max_task_revisions=1))
    checked = []

    def validate(path, **kwargs):
        checked.append(read_manifest(path)["task_version_id"])
        return validation(path)

    adapter = FakeAdapter(["MODEL_FAIL", "MODEL_FAIL"])
    summary = run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter, validator=validate)
    assert len(summary["revisions"]) == 1
    assert len(checked) == 2
    assert checked[0] != checked[1]
    with Registry(tmp_path / "registry.sqlite") as registry:
        trials = registry.list_trials(config.campaign_id)
        assert trials[1]["parent_task_version_id"] == trials[0]["task_version_id"]


def test_holdout_is_rejected_before_adapter_or_output_creation(tmp_path):
    task = generate_task("scientific-etl", 18, tmp_path / "tasks", cohort="holdout")
    adapter = FakeAdapter([])
    with pytest.raises(ValueError, match="cohort"):
        run_campaign(configuration(task), tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter)
    assert not (tmp_path / "run").exists()


def test_existing_campaign_cannot_reset_budget_and_rerun(tmp_path):
    task = generate_task("repository-debug", 19, tmp_path / "tasks")
    config = configuration(task)
    adapter = FakeAdapter(["PASS"])
    run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter, validator=validation)
    with pytest.raises(ValueError, match="already has trials"):
        run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "again", adapter=adapter, validator=validation)
    assert len(adapter.calls) == 1


def test_budget_is_checked_before_any_model_call(tmp_path):
    task = generate_task("scientific-etl", 20, tmp_path / "tasks")
    config = configuration(task, budgets={})
    adapter = FakeAdapter([])
    summary = run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter, validator=validation)
    assert not adapter.calls
    assert summary["status"] == "budget_stopped"


def test_fixed_suite_runs_complete_preregistered_matrix_without_discovery_selection(tmp_path):
    from harborforge.evaluation.frozen import freeze_suite, preregistration_config

    tasks = [generate_task("repository-debug", n, tmp_path / "tasks", cohort="holdout") for n in (31, 32)]
    value = configuration(tasks[0]).model_dump(mode="json")
    value.update(objective="fixed_evaluation", cohort="holdout", tasks=list(map(str, tasks)),
                 suite_manifest=str(tmp_path / "suite.json"))
    value["model_profiles"].append({**value["model_profiles"][0], "profile_id": "test-model-2", "tier": 2,
                                    "model_id": "test/second", "family": "second"})
    config = CampaignConfig.model_validate(value)
    freeze_suite(tasks, tmp_path / "suite.json", preregistration_config(config.model_dump(mode="json")))
    adapter = FakeAdapter(["PASS"] * 4)
    summary = run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter,
                           validator=validation, fixed=True)
    assert len(adapter.calls) == 4
    assert not summary["revisions"]
    with Registry(tmp_path / "registry.sqlite") as registry:
        trials = registry.list_trials(config.campaign_id)
        assert len({(trial["task_version_id"], trial["model_profile_id"]) for trial in trials}) == 4


def test_later_tier_does_not_regenerate_existing_child(tmp_path):
    task = generate_task("repository-debug", 33, tmp_path / "tasks")
    value = configuration(task).model_dump(mode="json")
    value["budgets"]["max_task_revisions"] = 3
    value["model_profiles"].append({**value["model_profiles"][0], "profile_id": "test-second", "tier": 2})
    adapter = FakeAdapter(["MODEL_FAIL", "PASS", "MODEL_FAIL"])
    summary = run_campaign(CampaignConfig.model_validate(value), tmp_path / "registry.sqlite", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert summary["status"] == "complete"
    assert len(summary["revisions"]) == 1
    assert len(adapter.calls) == 3


def test_validation_infrastructure_error_is_not_an_invalid_task_or_model_failure(tmp_path):
    task = generate_task("repository-debug", 34, tmp_path / "tasks")
    adapter = FakeAdapter([])
    summary = run_campaign(configuration(task), tmp_path / "registry.sqlite", tmp_path / "run", adapter=adapter,
        validator=lambda path, **kw: {**validation(path), "valid": False, "status": "VALIDATION_ERROR"})
    assert summary["quality_errors"] == 1
    assert summary["quality_invalid"] == 0
    assert summary["status"] == "validation_error"
    with Registry(tmp_path / "registry.sqlite") as registry:
        assert registry.list_trials("contract-test")[0]["outcome"] == "INFRA_ERROR"


def test_malformed_adapter_response_is_recorded_and_stops_unknown_usage(tmp_path):
    task = generate_task("repository-debug", 35, tmp_path / "tasks")

    class Malformed:
        def run(self, *args):
            return None

    summary = run_campaign(configuration(task), tmp_path / "registry.sqlite", tmp_path / "run",
                           adapter=Malformed(), validator=validation)
    assert summary["status"] == "usage_reconciliation_required"
    with Registry(tmp_path / "registry.sqlite") as registry:
        assert registry.list_trials("contract-test")[0]["outcome"] == "UNSCORABLE"
