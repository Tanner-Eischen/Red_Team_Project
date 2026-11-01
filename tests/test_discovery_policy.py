"""Offline policy contracts: all outcomes and Docker validators here are injected fixtures."""

import json

import pytest
from pydantic import ValidationError

from harborforge.authoring.mutations import select_mutation
from harborforge.authoring.provenance import read_manifest
from harborforge.config import CampaignConfig
from harborforge.execution.campaign import run_campaign
from harborforge.execution.policy import candidate_state
from harborforge.storage.registry import Registry
from harborforge.task_families.registry import generate_task


def config(task, *, campaign_id="policy-test", policy=None, **changes):
    value = dict(schema_version="2", campaign_id=campaign_id, tasks=[str(task)],
        harness_profile=dict(timeout_seconds=1, verifier_timeout_seconds=1, trial_time_limit_sec=5,
                             max_input_tokens=100, max_output_tokens=50),
        budgets=dict(campaign_usd_max=20, total_input_tokens_max=2000, total_output_tokens_max=1000,
                     max_model_attempts=20, wall_time_seconds_max=120),
        model_profiles=[dict(profile_id="policy-fixture", provider="unit-test", model_id="unit-test/fake",
            family="test", max_cost_usd_per_attempt=1, max_input_tokens=100, max_output_tokens=50,
            input_usd_per_million=2, output_usd_per_million=4,
            price_source="offline fixture, no provider", price_checked_utc="2026-10-31T00:00:00Z")],
        discovery_policy={"max_revisions_per_lineage": 2, "max_revisions_total": 2,
            "max_generation_attempts": 2, "max_no_progress": 1, "max_attempts_per_tier": 12,
            "all_pass_operator_ids": ["repository-boundary-overlap"], **(policy or {})})
    value.update(changes)
    return CampaignConfig.model_validate(value)


def validation(path, **kwargs):
    return dict(valid=True, runtime="docker", eligible_for_model_evaluation=True,
                task_sha256=read_manifest(path)["artifact_sha256"], status="VALIDATED", gates=[])


class RecordingAdapter:
    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def run(self, path, model, harness, output):
        self.calls.append((read_manifest(path)["task_version_id"], model["profile_id"]))
        outcome = next(self.outcomes)
        return dict(outcome=outcome, reward=1 if outcome == "PASS" else 0 if outcome == "MODEL_FAIL" else None,
                    cost_usd=.01, tokens_input=20, tokens_output=10, wall_time_s=.1)


def test_all_pass_child_is_immutable_revalidated_and_retested(tmp_path):
    task = generate_task("repository-debug", 41, tmp_path / "tasks")
    before = read_manifest(task)
    adapter = RecordingAdapter("PASS", "PASS", "PASS")
    checked = []

    def validate(path, **kwargs):
        checked.append(read_manifest(path)["task_version_id"])
        return validation(path)

    summary = run_campaign(config(task), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validate)
    assert len(summary["revisions"]) == 1 and len(adapter.calls) == len(checked) == 2
    assert checked[0] != checked[1]
    assert read_manifest(task) == before
    assert summary["execution_mode"] == "offline_contract_test"
    assert summary["revisions"][0]["selection"]["mode"] == "all_pass"
    assert summary["revisions"][0]["difficulty_measured"] is False
    assert any(row["reason"] == "no-progress ceiling reached" for row in summary["candidate_transitions"])
    with Registry(tmp_path / "registry.db") as registry:
        for transition in summary["candidate_transitions"]:
            encoded = registry.artifacts.get(transition["artifact_sha256"])
            assert json.loads(encoded)["sequence"] == transition["sequence"]
        assert all(row["execution_mode"] == "offline_contract_test" for row in registry.list_trials("policy-test"))


@pytest.mark.parametrize("outcome", ["PROVIDER_ERROR", "INFRA_ERROR", "UNSCORABLE", "AGENT_TIMEOUT"])
def test_incomplete_or_timeout_evidence_never_drives_mutation(tmp_path, outcome):
    task = generate_task("repository-debug", 42, tmp_path / "tasks")
    adapter = RecordingAdapter(outcome)
    summary = run_campaign(config(task), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert summary["revisions"] == [] and len(adapter.calls) == 1
    assert summary["candidate_transitions"][-1]["state"] in {"timed_out", "evidence_incomplete"}


def test_provider_retry_does_not_hide_incomplete_evidence(tmp_path):
    task = generate_task("repository-debug", 43, tmp_path / "tasks")
    value = config(task).model_dump(mode="json")
    value["budgets"]["retry_provider_errors"] = 1
    adapter = RecordingAdapter("PROVIDER_ERROR", "PASS")
    summary = run_campaign(CampaignConfig.model_validate(value), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert len(adapter.calls) == 2 and not summary["revisions"]
    assert summary["candidate_transitions"][-1]["state"] == "evidence_incomplete"


def test_per_tier_attempt_limit_stops_before_unplanned_extra_calls(tmp_path):
    task = generate_task("repository-debug", 44, tmp_path / "tasks")
    value = config(task, policy={"max_attempts_per_tier": 1}).model_dump(mode="json")
    value["model_profiles"][0]["repeats"] = 2
    adapter = RecordingAdapter("PASS", "PASS")
    summary = run_campaign(CampaignConfig.model_validate(value), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert len(adapter.calls) == 1 and summary["status"] == "policy_stopped"
    assert summary["candidate_transitions"][-1]["action"] == "stop_campaign"


@pytest.mark.parametrize("limit,reason", [("max_generation_attempts", "generation attempt ceiling"),
                                          ("max_revisions_per_lineage", "lineage revision ceiling"),
                                          ("max_revisions_total", "total revision ceiling")])
def test_zero_revision_and_generation_limits_fail_closed(tmp_path, limit, reason):
    task = generate_task("repository-debug", 45, tmp_path / "tasks")
    adapter = RecordingAdapter("PASS")
    summary = run_campaign(config(task, policy={limit: 0}), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert len(adapter.calls) == 1 and summary["generation_attempts"] == 0 and not summary["revisions"]
    assert reason in summary["candidate_transitions"][-1]["reason"]


def test_existing_child_is_reused_and_matched_at_the_next_tier(tmp_path):
    task = generate_task("repository-debug", 46, tmp_path / "tasks")
    value = config(task, policy={"exploration_quota": 1, "max_revisions_per_lineage": 1}).model_dump(mode="json")
    value["model_profiles"].append({**value["model_profiles"][0], "profile_id": "policy-fixture-2", "tier": 2})
    adapter = RecordingAdapter("PASS", "PASS", "MODEL_FAIL", "PASS")
    summary = run_campaign(CampaignConfig.model_validate(value), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert len(adapter.calls) == 4 and len(summary["revisions"]) == 1
    assert adapter.calls[0][0] == adapter.calls[2][0] and adapter.calls[1][0] == adapter.calls[3][0]
    assert summary["exploration_promotions"] == 1
    assert any(row["action"] == "matched_retest" for row in summary["candidate_transitions"])


def test_failure_mutation_requires_latest_confirmed_finding_on_exact_parent(tmp_path):
    task = generate_task("repository-debug", 47, tmp_path / "tasks")
    db = tmp_path / "registry.db"
    seed = config(task, campaign_id="seed-finding", policy={"all_pass_operator_ids": []})
    initial = run_campaign(seed, db, tmp_path / "seed", adapter=RecordingAdapter("MODEL_FAIL"), validator=validation)
    assert not initial["revisions"]
    with Registry(db) as registry:
        trial = registry.list_trials("seed-finding")[0]
        finding = registry.record_finding(trial["trial_id"], "behavioral_assertion", causal_claim=False)
        registry.review_finding(finding, "offline-unit-reviewer", "confirmed", "Fixture review; no real model evidence")
    summary = run_campaign(config(task, policy={"reviewed_finding_ids": [finding], "all_pass_operator_ids": []}), db, tmp_path / "reviewed",
                           adapter=RecordingAdapter("MODEL_FAIL", "PASS"), validator=validation)
    assert len(summary["revisions"]) == 1
    selection = summary["revisions"][0]["selection"]
    assert selection["operator_id"] == "repository-boundary-overlap" and selection["causal_claim"] is False
    assert selection["evidence"][0]["trial_id"] == trial["trial_id"]
    with Registry(db) as registry:
        registry.review_finding(finding, "offline-unit-reviewer", "rejected", "Counterevidence supersedes confirmation")
    rejected = run_campaign(config(task, campaign_id="review-rejected", policy={"reviewed_finding_ids": [finding]}),
                            db, tmp_path / "rejected", adapter=RecordingAdapter("MODEL_FAIL"), validator=validation)
    assert not rejected["revisions"] and rejected["candidate_transitions"][-1]["state"] == "review_required"


def test_operator_selection_changes_with_reviewed_mechanism(tmp_path):
    parent = read_manifest(generate_task("scientific-etl", 48, tmp_path / "tasks"))
    evidence = dict(finding_id="finding-unit", trial_id="trial-unit", reviewer="offline-unit", decision="confirmed")
    numerical = select_mutation(parent, mode="reviewed_failure",
                                reviewed_evidence=[{**evidence, "category": "numerical_or_units"}])
    parsing = select_mutation(parent, mode="reviewed_failure",
                              reviewed_evidence=[{**evidence, "category": "parsing_or_schema"}])
    assert numerical["operator_id"] == "etl-mixed-unit-ordering"
    assert parsing["operator_id"] == "etl-nullable-revisions"
    assert select_mutation(parent, mode="reviewed_failure", reviewed_evidence=[]) is None


def test_configuration_migration_and_frozen_policy_rejection(tmp_path):
    task = generate_task("repository-debug", 49, tmp_path / "tasks")
    value = config(task).model_dump(mode="json")
    with pytest.raises(ValidationError, match="migration"):
        CampaignConfig.model_validate({**value, "schema_version": "1"})
    with pytest.raises(ValidationError, match="explicit discovery_policy"):
        CampaignConfig.model_validate({**value, "discovery_policy": None})
    with pytest.raises(ValidationError, match="legacy max_task_revisions"):
        CampaignConfig.model_validate({**value, "budgets": {**value["budgets"], "max_task_revisions": 1}})
    with pytest.raises(ValidationError, match="adaptive discovery policy"):
        CampaignConfig.model_validate({**value, "objective": "fixed_evaluation", "cohort": "holdout", "suite_manifest": "suite.json"})
    legacy = CampaignConfig.model_validate({**value, "schema_version": "1", "discovery_policy": None,
                                           "budgets": {**value["budgets"], "max_task_revisions": 2}})
    adapter = RecordingAdapter("PASS")
    result = run_campaign(legacy, tmp_path / "registry.db", tmp_path / "legacy", adapter=adapter, validator=validation)
    assert not result["revisions"] and len(adapter.calls) == 1


@pytest.mark.parametrize("outcomes,expected", [(["PASS"], "all_pass"), (["MODEL_FAIL"], "all_fail"),
    (["PASS", "MODEL_FAIL"], "mixed"), (["PROVIDER_ERROR"], "evidence_incomplete"),
    (["AGENT_TIMEOUT"], "timed_out"), ([], "evidence_incomplete")])
def test_pure_observed_states(outcomes, expected):
    assert candidate_state({"valid": True}, [{"outcome": value} for value in outcomes]) == expected


@pytest.mark.parametrize("schema", ["1", "2"])
def test_global_provider_retry_quota_preserves_all_remaining_primaries(tmp_path, schema):
    task = generate_task("repository-debug", 50, tmp_path / "tasks")
    value = config(task).model_dump(mode="json")
    value["schema_version"] = schema
    if schema == "1":
        value["discovery_policy"] = None
    value["budgets"].update(retry_provider_errors=1, max_provider_retries_total=2)
    value["model_profiles"][0]["repeats"] = 4
    adapter = RecordingAdapter("PROVIDER_ERROR", "PASS", "PROVIDER_ERROR", "PASS", "PROVIDER_ERROR", "PROVIDER_ERROR")
    summary = run_campaign(CampaignConfig.model_validate(value), tmp_path / "registry.db", tmp_path / "run",
                           adapter=adapter, validator=validation)
    assert len(adapter.calls) == 6
    assert summary["provider_retries_started"] == summary["provider_retries_skipped"] == 2
    with Registry(tmp_path / "registry.db") as registry:
        trials = registry.list_trials("policy-test")
        assert {row["repeat_index"] for row in trials} == {0, 1, 2, 3}
        assert sum(row["attempt_number"] > 0 for row in trials) == 2
        events = [event for trial in trials for event in registry.list_events(trial["trial_id"])]
        assert sum(event["event_type"] == "provider_retry_quota_exhausted" for event in events) == 2
    assert summary["budget"]["attempts"] == 6


def test_failed_quality_is_quarantined_without_calling_adapter(tmp_path):
    task = generate_task("repository-debug", 51, tmp_path / "tasks")
    adapter = RecordingAdapter()
    summary = run_campaign(config(task), tmp_path / "registry.db", tmp_path / "run", adapter=adapter,
        validator=lambda path, **kw: {**validation(path), "valid": False})
    assert not adapter.calls and summary["quality_invalid"] == 1 and not summary["revisions"]
    assert summary["candidate_transitions"][-1]["state"] == "quality_failed"


@pytest.mark.parametrize("prior", ["quality_failed", "validation_error", "evidence_incomplete", "timed_out", "limit_reached"])
def test_untrusted_or_terminal_states_cannot_transition_to_child(prior):
    from harborforge.execution.policy import validate_transition
    with pytest.raises(ValueError, match="Unsupported candidate transition"):
        validate_transition(prior, "child_created")


@pytest.mark.parametrize("timestamps", [
    ["2026-11-01T12:00:00Z"] * 3,
    ["2026-11-02T12:00:00Z", "2026-11-01T12:00:00Z", "2026-10-31T12:00:00Z"],
])
def test_review_authority_follows_append_order_despite_equal_or_rollback_clocks(tmp_path, monkeypatch, timestamps):
    import types
    import harborforge.storage.registry as registry_module
    from harborforge.execution.policy import reviewed_evidence

    task = generate_task("repository-debug", 52, tmp_path / "tasks")
    db = tmp_path / "registry.db"
    run_campaign(config(task, policy={"all_pass_operator_ids": []}), db, tmp_path / "run",
                 adapter=RecordingAdapter("MODEL_FAIL"), validator=validation)
    with Registry(db) as registry:
        trial = registry.list_trials("policy-test")[0]
        finding = registry.record_finding(trial["trial_id"], "behavioral_assertion")
        moments = iter(timestamps)
        identifiers = iter(["f" * 32, "0" * 32, "1" * 32])
        with monkeypatch.context() as patch:
            patch.setattr(registry_module, "utc_now", lambda: next(moments))
            patch.setattr(registry_module.uuid, "uuid4", lambda: types.SimpleNamespace(hex=next(identifiers)))
            registry.review_finding(finding, "offline-reviewer", "confirmed", "Initial unit-fixture review")
            registry.review_finding(finding, "offline-reviewer", "rejected", "Latest appended revocation")
            assert reviewed_evidence(registry, trial["task_version_id"], (finding,)) == []
            latest = registry.review_finding(finding, "offline-reviewer", "confirmed", "New appended re-review")
        selected = reviewed_evidence(registry, trial["task_version_id"], (finding,))
        assert len(selected) == 1 and selected[0]["review_id"] == latest


def test_saturated_raster_proposal_stops_before_unchanged_child_retest(tmp_path):
    pytest.importorskip("rasterio")
    task = generate_task("geospatial-raster", 123, tmp_path / "tasks")
    original = read_manifest(task)
    adapter = RecordingAdapter("PASS", "PASS", "PASS")
    summary = run_campaign(config(task, policy={"max_no_progress": 5, "max_revisions_per_lineage": 10,
        "max_revisions_total": 10, "max_generation_attempts": 10,
        "all_pass_operator_ids": ["raster-non-square-spacing"]}), tmp_path / "registry.db", tmp_path / "run",
        adapter=adapter, validator=validation)
    assert len(adapter.calls) == 3 and len(summary["revisions"]) == 2 and summary["generation_attempts"] == 3
    assert summary["candidate_transitions"][-1]["state"] == "no_semantic_change"
    assert len(list((tmp_path / "run/revisions").iterdir())) == 2
    assert read_manifest(task) == original
