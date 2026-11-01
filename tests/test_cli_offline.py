"""Read-only proposal arithmetic and public-export boundaries through the CLI."""

import copy
import json
from pathlib import Path

import pytest

from harborforge.analysis.budget_proposal import estimate_proposal
from harborforge.cli import main
from harborforge.storage.registry import Registry


@pytest.fixture
def proposal():
    return json.loads((Path(__file__).parents[1] / "docs/proposals/hf02-pilot-2026-11-01.json").read_text())


def test_pilot_declares_primary_retry_and_campaign_reservations(proposal):
    result = estimate_proposal(proposal)
    assert result["planned_attempts"] == 12
    assert result["maximum_total_attempts"] == 14
    assert result["primary_token_envelope_usd"] == 8.736
    assert result["primary_reservations_usd"] == 9.3
    assert result["total_reservations_usd"] == 11.7
    assert result["campaign_hard_cap_usd"] == 15
    assert result["model_run_authorized"] is False


@pytest.mark.parametrize("change,match", [
    ({"campaign_usd_max": 10}, "cap cannot cover"),
    ({"max_model_attempts": 24}, "attempt ceiling"),
    ({"total_input_tokens_max": 256000}, "input envelope"),
    ({"total_output_tokens_max": 32000}, "output envelope"),
    ({"retry_provider_errors": 0}, "retry allowance"),
    ({"max_provider_retries_total": None}, "global provider-retry ceiling"),
    ({"max_provider_retries_total": 3}, "global provider-retry ceiling"),
])
def test_proposal_cannot_hide_underfunded_or_undeclared_attempts(proposal, change, match):
    proposal["campaign"]["budgets"].update(change)
    with pytest.raises(ValueError, match=match):
        estimate_proposal(proposal)


def test_proposal_cannot_be_mistaken_for_execution_authorization(proposal):
    proposal["approval_status"] = "approved"
    with pytest.raises(ValueError, match="proposed_not_authorized"):
        estimate_proposal(proposal)


def test_budget_cli_has_no_network_or_registry_side_effect(proposal, tmp_path, monkeypatch, capsys):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("A proposal must not contact a provider or initialize a registry")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(Registry, "__init__", forbidden)
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal))
    assert main(["campaign", "budget", "--proposal", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["model_requests_executed"] == 0
    assert result["approval_status"] == "proposed_not_authorized"


@pytest.mark.parametrize("cohort", ["holdout", "calibration"])
def test_cli_refuses_public_export_of_private_cohorts(tmp_path, cohort, capsys):
    registry_path = tmp_path / "registry.sqlite"
    with Registry(registry_path) as registry:
        registry.create_campaign({"campaign_id": "private", "objective": "fixed_evaluation", "cohort": cohort})
    output = tmp_path / "public"
    assert main(["report", "build", "--campaign", "private", "--registry", str(registry_path),
                 "--out", str(output), "--public"]) == 2
    assert "public" in capsys.readouterr().err.lower()
    assert not output.exists()


def test_proposal_refuses_embedded_executable_task_paths(proposal):
    value = copy.deepcopy(proposal)
    value["campaign"]["tasks"] = ["tasks/some-task"]
    with pytest.raises(ValueError, match="not an executable campaign"):
        estimate_proposal(value)


def test_proposal_refuses_adaptive_policy_and_tier_selection(proposal):
    value = copy.deepcopy(proposal)
    value["campaign"]["schema_version"] = "2"
    value["campaign"]["discovery_policy"] = {}
    with pytest.raises(ValueError, match="adaptive discovery policy"):
        estimate_proposal(value)
    proposal["campaign"]["model_profiles"][1]["tier"] = 2
    with pytest.raises(ValueError, match="share one tier"):
        estimate_proposal(proposal)
