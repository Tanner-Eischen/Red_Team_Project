"""Offline request-limit and pinned-Terminus accounting regressions; no model API."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harborforge.adapters.call_budget import AgentBudgetExceededError, BudgetedLLM, CallBudget
from harborforge.adapters.harbor_adapter import classify_exception, parse_trial_result
from harborforge.adapters.openrouter_free import OpenRouterFreeLLM
from harborforge.config import CampaignConfig
from harborforge.execution.campaign import run_campaign
from harborforge.storage import Registry
from harborforge.task_families.registry import generate_task

from test_campaign import validation
from test_openrouter_free import MODEL, policy as recorded_policy, response


@pytest.fixture
def free_policy(tmp_path):
    return recorded_policy.__wrapped__(tmp_path)


def test_cap_stops_before_reservation_or_dispatch_and_keeps_prior_known_accounting():
    async def forbidden(**kwargs):
        raise AssertionError("Exhausted cap must not dispatch")

    free = OpenRouterFreeLLM(SimpleNamespace(), MODEL, {"max_requests_per_attempt": 12}, completion=forbidden)
    free.requests = 12
    budget = CallBudget(10000, 1000, 0, 0, 0, input_reserved=4000, output_reserved=200)
    before = asdict(budget)
    with pytest.raises(AgentBudgetExceededError) as caught:
        asyncio.run(BudgetedLLM(free, budget, 100).call("No thirteenth request"))
    assert asdict(budget) == before
    assert not budget.usage_uncertain and free.requests == 12
    assert classify_exception({"exception_type": type(caught.value).__name__}) == ("AGENT_TIMEOUT", "budget_exhausted")


@pytest.fixture
def agent(free_policy, tmp_path, monkeypatch):
    # Use the installed pinned constructor and run/finally. Only the terminal
    # and episode callback are recording fixtures; socket/model dispatch forbid.
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    # Windows asyncio creates a local socketpair when the event loop starts.
    # Construct that loop before forbidding every subsequent socket connection.
    loop = asyncio.new_event_loop()
    import socket

    def forbidden_network(*args, **kwargs):
        raise AssertionError("Offline accounting regression must not access the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    import litellm
    from harborforge.adapters.budgeted_agent import BudgetedTerminus2

    async def forbidden_provider(**kwargs):
        raise AssertionError("Only recording completions are allowed")

    monkeypatch.setattr(litellm, "acompletion", forbidden_provider)
    logs = tmp_path / "agent"
    logs.mkdir()
    value = BudgetedTerminus2(logs_dir=logs, model_name=MODEL, free_routing=free_policy,
        campaign_budget={"max_input_tokens": 100000, "max_output_tokens": 8000,
                         "max_cost_usd": 0, "input_usd_per_million": 0, "output_usd_per_million": 0})
    value._session = SimpleNamespace(get_incremental_output=AsyncMock(return_value="Recording terminal"))
    clock = [0.0]

    async def no_wait(seconds):
        clock[0] += seconds

    value._free_llm._clock = lambda: clock[0]
    value._free_llm._sleep = no_wait

    async def recorded_completion(**kwargs):
        return response()

    value._free_llm._completion = recorded_completion
    value._recording_test_loop = loop
    try:
        yield value
    finally:
        loop.close()


def run_recorded_loop(agent, monkeypatch, *, exhausted=False, corrupt=None):
    from harbor.models.agent.context import AgentContext

    async def episode(*, initial_prompt, chat, original_instruction):
        await chat.chat("First recording-only completion")
        await chat.chat("Second recording-only completion")
        if corrupt is not None:
            corrupt(agent)
        if exhausted:
            await chat.chat("Known pre-dispatch request ceiling")

    monkeypatch.setattr(agent, "_run_agent_loop", episode)
    context = AgentContext()
    if exhausted:
        with pytest.raises(AgentBudgetExceededError):
            agent._recording_test_loop.run_until_complete(agent.run("Offline fixture task", SimpleNamespace(), context))
    else:
        agent._recording_test_loop.run_until_complete(agent.run("Offline fixture task", SimpleNamespace(), context))
    return context


@pytest.mark.parametrize("exhausted", [False, True])
def test_real_pinned_terminus_finally_restores_only_observed_zero_cost(agent, monkeypatch, exhausted):
    context = run_recorded_loop(agent, monkeypatch, exhausted=exhausted)
    assert context.cost_usd == 0.0
    assert context.n_input_tokens == 40 and context.n_output_tokens == 8
    assert context.metadata["free_only"]["usage_verified"] is True
    assert context.metadata["free_only"]["requests"] == 2
    assert not context.metadata["budget_reservation"]["usage_uncertain"]
    assert len(context.metadata["free_only"]["provider_usage"]) == 2


@pytest.mark.parametrize("corrupt", [
    lambda agent: setattr(agent._free_llm, "requests", 3),
    lambda agent: agent._free_llm.audit[0].pop("prompt_tokens"),
    lambda agent: agent._free_llm.audit[0].update(provider_cost_usd=1e-13),
    lambda agent: agent._free_llm.audit[0].update(completion_tokens=True),
    lambda agent: agent._free_llm.audit[0].update(pricing_evidence_verified=False),
    lambda agent: setattr(agent._campaign_budget, "usage_uncertain", True),
])
def test_missing_failed_or_unverified_audit_never_invents_zero(agent, monkeypatch, corrupt):
    context = run_recorded_loop(agent, monkeypatch, corrupt=corrupt)
    assert context.cost_usd is None  # The pinned Terminus behavior is retained.
    assert context.metadata["free_only"]["usage_verified"] is False


def test_known_cap_timeout_does_not_stop_remaining_campaign_trials(agent, free_policy, tmp_path, monkeypatch):
    context = run_recorded_loop(agent, monkeypatch, exhausted=True)
    parsed = parse_trial_result({"agent_result": context.model_dump(), "exception_info": {
        "exception_type": "AgentBudgetExceededError", "exception_message": "Known cap, no dispatch"}})
    assert parsed["outcome"] == "AGENT_TIMEOUT" and parsed["cost_usd"] == 0
    assert parsed["tokens_input"] == 40 and parsed["tokens_output"] == 8
    task = generate_task("repository-debug", 20261102, tmp_path / "tasks")
    config = CampaignConfig.model_validate({"campaign_id": "known-free-cap-offline-witness",
        "tasks": [str(task)], "harness_profile": {"max_input_tokens": 100000, "max_output_tokens": 8000,
            "trial_time_limit_sec": 5}, "budgets": {"campaign_usd_max": 0, "max_model_attempts": 2,
            "total_input_tokens_max": 200000, "total_output_tokens_max": 16000, "wall_time_seconds_max": 120,
            "max_provider_retries_total": 0}, "model_profiles": [{"profile_id": "free-cap-offline-witness",
            "provider": "openrouter", "model_id": MODEL, "family": "recording", "repeats": 2,
            "max_cost_usd_per_attempt": 0, "billing_mode": "free_only", "free_routing": free_policy,
            "max_input_tokens": 100000, "max_output_tokens": 8000, "input_usd_per_million": 0,
            "output_usd_per_million": 0, "price_source": "Offline recording fixture", "price_checked_utc": "2026-11-02"}]})

    class RecordedResults:
        calls = 0

        def run(self, *args):
            self.calls += 1
            result = parsed if self.calls == 1 else {"outcome": "PASS", "reward": 1,
                "tokens_input": 10, "tokens_output": 2, "cost_usd": 0}
            return {**result, "wall_time_s": 0.01}

    recorded = RecordedResults()
    registry_path = tmp_path / "registry.sqlite"
    summary = run_campaign(config, registry_path, tmp_path / "run", adapter=recorded, validator=validation)
    assert summary["status"] == "complete" and recorded.calls == 2
    assert summary["budget"]["usage_uncertain"] is False
    assert summary["budget"]["reserved_or_measured_cost_usd"] == 0
    with Registry(registry_path) as registry:
        assert [trial["outcome"] for trial in registry.list_trials(config.campaign_id)] == ["AGENT_TIMEOUT", "PASS"]
