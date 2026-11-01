import math

import pytest
from pydantic import ValidationError

from harborforge.config import Budgets, CampaignConfig, ModelProfile, load_campaign
from harborforge.execution.budget import BudgetExceeded, BudgetLedger


def model(**overrides):
    return ModelProfile.model_validate({"profile_id": "test-local", "provider": "test", "model_id": "local/test",
        "family": "test", "max_cost_usd_per_attempt": 1.0, "max_input_tokens": 100,
        "max_output_tokens": 50, "input_usd_per_million": 2, "output_usd_per_million": 4,
        "price_source": "unit-test fixture", "price_checked_utc": "2026-10-31T00:00:00Z", **overrides})


def ledger(**overrides):
    return BudgetLedger(Budgets(campaign_usd_max=2, total_input_tokens_max=200,
        total_output_tokens_max=100, max_model_attempts=2, **overrides))


def test_reserve_before_call_cannot_oversubscribe():
    budget = ledger()
    budget.reserve(model(), 10)
    budget.reserve(model(), 10)
    with pytest.raises(BudgetExceeded):
        budget.reserve(model(), 10)
    assert budget.attempts == 2


@pytest.mark.parametrize("missing", ["cost_usd", "tokens_input", "tokens_output"])
def test_unknown_usage_stops_next_call(missing):
    budget = ledger()
    reservation = budget.reserve(model(), 10)
    result = {"cost_usd": 0.1, "tokens_input": 10, "tokens_output": 5}
    result[missing] = None
    budget.settle(reservation, result)
    with pytest.raises(BudgetExceeded, match="unknown usage"):
        budget.reserve(model(), 10)
    assert budget.cost == 1


def test_settlement_releases_only_measured_reservations():
    budget = ledger()
    reservation = budget.reserve(model(), 10)
    budget.settle(reservation, {"cost_usd": 0.2, "tokens_input": 20, "tokens_output": 10})
    assert budget.snapshot()["reserved_or_measured_cost_usd"] == pytest.approx(0.2)
    assert budget.input_tokens == 20


@pytest.mark.parametrize("value", [math.nan, math.inf, -1, True])
def test_bad_usage_is_not_refunded(value):
    budget = ledger()
    reservation = budget.reserve(model(), 10)
    budget.settle(reservation, {"cost_usd": value, "tokens_input": 20, "tokens_output": 10})
    assert budget.uncertain
    assert budget.cost == 1


def test_wall_time_must_cover_full_attempt():
    budget = ledger(wall_time_seconds_max=1)
    with pytest.raises(BudgetExceeded, match="wall-time"):
        budget.reserve(model(), 10)


def test_zero_default_cannot_spend():
    with pytest.raises(BudgetExceeded):
        BudgetLedger(Budgets()).reserve(model(), 10)


def test_immutable_config_and_split_boundaries():
    with pytest.raises(ValidationError, match="discovery"):
        CampaignConfig(campaign_id="bad", cohort="holdout")
    with pytest.raises(ValidationError, match="frozen"):
        CampaignConfig(campaign_id="bad", objective="fixed_evaluation")
    with pytest.raises(ValidationError):
        model(max_cost_usd_per_attempt=0.000001)
    with pytest.raises(ValidationError):
        model(model_id="REPLACE_WITH_MODEL")


def test_config_paths_are_relative_to_config(tmp_path):
    path = tmp_path / "campaign.yaml"
    path.write_text("campaign_id: path-test\ntasks: [task]\n", encoding="utf-8")
    cfg = load_campaign(path)
    assert cfg.tasks == (str((tmp_path / "task").resolve()),)
