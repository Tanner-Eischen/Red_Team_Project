"""Strict zero-price configuration/arithmetic witnesses; no providers or credentials."""

import socket

import pytest
from pydantic import ValidationError

from harborforge.analysis.budget_proposal import estimate_proposal
from harborforge.config import CampaignConfig, ModelProfile, OpenRouterFreePolicy, load_campaign
from harborforge.execution.budget import BudgetExceeded, BudgetLedger


def policy(model_id="openrouter/unit-test/model:free"):
    raw = model_id.removeprefix("openrouter/")
    return dict(allowed_model_ids=[model_id], response_model_ids=[raw, raw.removesuffix(":free")],
        catalog_path="catalog.json", catalog_sha256="a" * 64,
        endpoints_path="endpoints.json", endpoints_sha256="b" * 64,
        account_evidence_path="account.json", account_evidence_sha256="c" * 64,
        verified_at_utc="2026-11-02T00:00:00Z", max_age_seconds=3600,
        max_requests_per_attempt=12, minimum_request_interval_seconds=4,
        allow_fallbacks=False, allow_paid_fallback=False, max_usd_per_request=0)


def free_model(index=1, **changes):
    model_id = f"openrouter/unit-test/model-{index}:free"
    value = dict(profile_id=f"free-unit-{index}", provider="openrouter", model_id=model_id,
        family="offline-unit", tier=index, repeats=2, supports_tools=True,
        max_cost_usd_per_attempt=0, input_usd_per_million=0, output_usd_per_million=0,
        max_input_tokens=100, max_output_tokens=50, price_source="offline schema fixture, not verified availability",
        price_checked_utc="2026-11-02T00:00:00Z", billing_mode="free_only", free_routing=policy(model_id))
    value.update(changes)
    return value


def campaign(**changes):
    value = dict(campaign_id="free-unit-calibration", objective="fixed_evaluation", cohort="calibration",
        tasks=["tasks/one", "tasks/two", "tasks/three"], suite_manifest="frozen.json",
        harness_profile=dict(max_input_tokens=100, max_output_tokens=50, trial_time_limit_sec=5),
        model_profiles=[free_model(index) for index in (1, 2, 3)],
        budgets=dict(campaign_usd_max=0, total_input_tokens_max=1800, total_output_tokens_max=900,
            max_model_attempts=18, retry_provider_errors=0, max_provider_retries_total=0, wall_time_seconds_max=120))
    value.update(changes)
    return value


def proposal():
    return dict(proposal_id="free-only-unit-proposal", approval_status="proposed_not_authorized",
        billing_scope="openrouter_verified_free_only", matrix_mode="frozen_calibration_matrix",
        task_families=["scientific-etl", "geospatial-raster", "repository-debug"], provider_retry_allowance=0,
        campaign=campaign())


def test_named_verified_free_profile_allows_genuine_zero_reservation():
    model = ModelProfile.model_validate(free_model())
    config = CampaignConfig.model_validate(campaign())
    ledger = BudgetLedger(config.budgets)
    reservation = ledger.reserve(model, 5)
    assert reservation.cost == 0 and ledger.attempts == 1
    ledger.settle(reservation, dict(cost_usd=0, tokens_input=10, tokens_output=5))
    assert ledger.cost == 0 and not ledger.uncertain
    assert ledger.reserve(model, 5).cost == 0


@pytest.mark.parametrize("change", [
    {"provider": "another-provider"}, {"billing_mode": "standard"}, {"free_routing": None},
    {"model_id": "openrouter/unit-test/model"}, {"model_id": "openrouter/free"},
    {"model_id": "openrouter/auto"}, {"model_id": "openrouter/openrouter/free:free"},
    {"model_id": "openrouter/unit-test/model-2:free"},
    {"max_cost_usd_per_attempt": 1e-13}, {"input_usd_per_million": 1e-13},
    {"output_usd_per_million": .01}, {"max_cost_usd_per_attempt": False},
    {"agent_kwargs": {"api_base": "https://untrusted.invalid"}},
])
def test_zero_mode_cannot_be_used_for_paid_generic_or_unverified_routes(change):
    with pytest.raises(ValidationError):
        ModelProfile.model_validate(free_model(**change))


@pytest.mark.parametrize("change", [
    {"allow_fallbacks": True}, {"allow_paid_fallback": True}, {"max_usd_per_request": 1e-100},
    {"max_usd_per_request": False}, {"provider_max_price": {"prompt": .001}},
    {"provider_max_price": {"request": .001}}, {"max_age_seconds": 7200},
    {"max_requests_per_attempt": 17}, {"minimum_request_interval_seconds": 1},
    {"verified_at_utc": "2026-11-02T00:00:00"}, {"verified_at_utc": "2026-11-02T00:00:00-06:00"},
    {"catalog_sha256": "invalid"}, {"catalog_path": "https://example.invalid/catalog.json"},
    {"account_evidence_path": ".env"}, {"response_model_ids": ["openrouter/auto"]},
    {"response_model_ids": ["openrouter/unit-test/model:free"]},
])
def test_policy_rejects_paid_fallback_weakened_guards_or_undated_evidence(change):
    with pytest.raises(ValidationError):
        OpenRouterFreePolicy.model_validate({**policy(), **change})


def test_zero_standard_profile_remains_rejected():
    value = free_model()
    value.pop("billing_mode")
    value.pop("free_routing")
    with pytest.raises(ValidationError, match="positive reservation"):
        ModelProfile.model_validate(value)


@pytest.mark.parametrize("change", [
    {"campaign_usd_max": .001}, {"retry_provider_errors": 1}, {"max_provider_retries_total": None},
    {"max_provider_retries_total": 1},
])
def test_free_campaign_requires_exact_zero_ceiling_and_explicit_no_auto_retries(change):
    value = campaign()
    value["budgets"].update(change)
    with pytest.raises(ValidationError):
        CampaignConfig.model_validate(value)


def test_free_campaign_cannot_mix_paid_profiles():
    value = campaign()
    other = value["model_profiles"][1]
    other.update(billing_mode="standard", free_routing=None, max_cost_usd_per_attempt=1)
    with pytest.raises(ValidationError, match="cannot mix"):
        CampaignConfig.model_validate(value)


def test_zero_dollars_does_not_remove_attempt_or_token_bounds():
    config = CampaignConfig.model_validate(campaign())
    limits = config.budgets.model_copy(update={"max_model_attempts": 1})
    ledger = BudgetLedger(limits)
    ledger.reserve(config.model_profiles[0], 5)
    with pytest.raises(BudgetExceeded, match="attempt"):
        ledger.reserve(config.model_profiles[0], 5)
    ledger = BudgetLedger(config.budgets.model_copy(update={"total_input_tokens_max": 99}))
    with pytest.raises(BudgetExceeded, match="input-token"):
        ledger.reserve(config.model_profiles[0], 5)


@pytest.mark.parametrize("cost", [1e-13, 1e-100, .001])
def test_zero_settlement_rejects_even_sub_epsilon_positive_usage(cost):
    config = CampaignConfig.model_validate(campaign())
    ledger = BudgetLedger(config.budgets)
    reservation = ledger.reserve(config.model_profiles[0], 5)
    ledger.settle(reservation, dict(cost_usd=cost, tokens_input=10, tokens_output=5))
    assert ledger.uncertain and ledger.cost == cost
    with pytest.raises(BudgetExceeded, match="unknown usage"):
        ledger.reserve(config.model_profiles[0], 5)


def test_free_matrix_arithmetic_retains_all_tiers_and_has_no_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Schema and arithmetic must not read evidence files or contact a provider")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = estimate_proposal(proposal())
    assert result["planned_attempts"] == result["maximum_total_attempts"] == 18
    assert result["maximum_provider_requests"] == 216
    assert [row["tier"] for row in result["models"]] == [1, 2, 3]
    assert result["primary_reservations_usd"] == result["total_reservations_usd"] == 0
    assert result["campaign_hard_cap_usd"] == result["per_request_hard_cap_usd"] == 0
    assert result["model_requests_executed"] == 0 and result["model_run_authorized"] is False


def test_free_matrix_does_not_silently_enable_adaptive_tier_selection():
    value = proposal()
    value["campaign"].update(objective="adversarial_discovery", cohort="discovery", suite_manifest=None)
    with pytest.raises(ValueError, match="separately frozen"):
        estimate_proposal(value)
    value = proposal()
    value["billing_scope"] = "standard_global_text_only_uncached"
    with pytest.raises(ValueError, match="restricted"):
        estimate_proposal(value)


def test_free_evidence_paths_resolve_relative_to_campaign_file(tmp_path):
    import yaml
    value = campaign()
    path = tmp_path / "config" / "campaign.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    config = load_campaign(path)
    policy_value = config.model_profiles[0].free_routing
    assert policy_value.catalog_path == str((path.parent / "catalog.json").resolve())
    assert policy_value.endpoints_path == str((path.parent / "endpoints.json").resolve())
    assert policy_value.account_evidence_path == str((path.parent / "account.json").resolve())


def test_missing_evidence_or_response_alias_must_not_be_silently_invented():
    for missing in ("catalog_path", "catalog_sha256", "endpoints_path", "endpoints_sha256",
                    "account_evidence_path", "account_evidence_sha256", "response_model_ids"):
        value = policy()
        value.pop(missing)
        with pytest.raises(ValidationError):
            OpenRouterFreePolicy.model_validate(value)
    value = free_model()
    value["free_routing"]["response_model_ids"] = ["unit-test/unrelated-model"]
    with pytest.raises(ValidationError, match="exact requested"):
        ModelProfile.model_validate(value)


def test_frozen_three_tier_zero_cost_matrix_runs_all_eighteen_offline_cells(tmp_path, monkeypatch):
    """Recording adapter and injected quality only; no provider or model measurement."""
    from harborforge.authoring.provenance import read_manifest
    from harborforge.evaluation.frozen import freeze_suite, preregistration_config
    from harborforge.execution.campaign import run_campaign
    from harborforge.storage.registry import Registry
    from harborforge.task_families.registry import FAMILIES, generate_task

    def forbidden(*args, **kwargs):
        raise AssertionError("An offline matrix witness must not contact a provider")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    tasks = [generate_task(family, 902102, tmp_path / "tasks", cohort="calibration") for family in FAMILIES]
    config = CampaignConfig.model_validate(campaign(tasks=list(map(str, tasks)), suite_manifest=str(tmp_path / "suite.json")))
    freeze_suite(tasks, config.suite_manifest, preregistration_config(config.model_dump(mode="json")))
    calls = []

    class RecordingFreeAdapter:
        def run(self, task, model, harness, output):
            calls.append((read_manifest(task)["task_version_id"], model["profile_id"]))
            return dict(outcome="PASS", reward=1, cost_usd=0, tokens_input=1, tokens_output=1, wall_time_s=.01)

    def injected_quality(path, **kwargs):
        return dict(valid=True, runtime="docker", eligible_for_model_evaluation=True,
                    task_sha256=read_manifest(path)["artifact_sha256"], status="VALIDATED", gates=[])

    result = run_campaign(config, tmp_path / "registry.sqlite", tmp_path / "run",
                          adapter=RecordingFreeAdapter(), validator=injected_quality, fixed=True)
    assert len(calls) == 18 and len(set(calls)) == 9
    assert result["status"] == "complete" and result["budget"]["attempts"] == 18
    assert result["budget"]["reserved_or_measured_cost_usd"] == 0 and not result["revisions"]
    assert result["execution_mode"] == "offline_contract_test"
    with Registry(tmp_path / "registry.sqlite") as registry:
        trials = registry.list_trials(config.campaign_id)
        assert len(trials) == 18
        assert all(row["cost_usd"] == 0 and row["execution_mode"] == "offline_contract_test" for row in trials)


def test_zero_campaign_ceiling_rejects_positive_sub_epsilon_reservation_before_issue():
    config = CampaignConfig.model_validate(campaign())
    ledger = BudgetLedger(config.budgets)
    positive = ModelProfile.model_validate(free_model(billing_mode="standard", free_routing=None,
                                                    max_cost_usd_per_attempt=5e-13))
    before = ledger.snapshot()
    with pytest.raises(BudgetExceeded, match="dollar budget"):
        ledger.reserve(positive, 5)
    assert ledger.snapshot() == before and ledger._pending == {}
    zero = ledger.reserve(config.model_profiles[0], 5)
    assert zero.cost == 0 and ledger.attempts == 1
