"""Accounted response errors and actual pinned-Terminus paths, with no model API."""

import asyncio
from types import SimpleNamespace

import pytest

from harborforge.adapters.call_budget import (
    BudgetedLLM, CallBudget, ProviderResponseError, ProviderUsageError,
)
from harborforge.adapters.harbor_adapter import classify_exception, parse_trial_result
from harborforge.adapters.openrouter_free import FreeOnlyRoutingError

from test_free_request_limits import agent as recorded_agent, free_policy as recorded_policy
from test_openrouter_free import response


def response_error(kind, *, usage_verified=True, **usage_changes):
    from harbor.llms.base import OutputLengthExceededError

    usage = SimpleNamespace(prompt_tokens=20, completion_tokens=4, cost_usd=0)
    for key, value in usage_changes.items():
        setattr(usage, key, value)
    error = (OutputLengthExceededError("Recorded output limit", truncated_response="") if kind == "length"
             else ProviderResponseError("Recorded malformed text"))
    error.usage, error.usage_verified = usage, usage_verified
    return error


@pytest.mark.parametrize("kind", ["length", "protocol"])
def test_trusted_response_error_keeps_usage_known_and_preserves_original_error(kind):
    error = response_error(kind)
    calls = []

    class RecordedCompletion:
        async def call(self, prompt, **kwargs):
            calls.append(prompt)
            raise error

    budget = CallBudget(30000, 1000, 0, 0, 0)
    guarded = BudgetedLLM(RecordedCompletion(), budget, 100)
    with pytest.raises(type(error)) as caught:
        asyncio.run(guarded.call("One explicitly requested completion"))
    assert caught.value is error and calls == ["One explicitly requested completion"]
    assert not budget.usage_uncertain and budget.cost_reserved == 0
    assert budget.output_reserved == 100  # A known failure never refunds its reservation.
    budget.reserve("A later registered request remains possible", {"max_tokens": 100})


@pytest.mark.parametrize("kind", ["length", "protocol"])
@pytest.mark.parametrize("changes", [
    {"prompt_tokens": -1}, {"prompt_tokens": 100000}, {"completion_tokens": 101},
    {"completion_tokens": True}, {"cost_usd": 1e-13}, {"cost_usd": None},
])
def test_attached_response_usage_still_must_fit_the_exact_reservation(kind, changes):
    error = response_error(kind, **changes)

    class RecordedCompletion:
        async def call(self, *args, **kwargs):
            raise error

    budget = CallBudget(30000, 1000, 0, 0, 0)
    with pytest.raises(ProviderUsageError):
        asyncio.run(BudgetedLLM(RecordedCompletion(), budget, 100).call("Recorded request"))
    assert budget.usage_uncertain


def test_known_response_error_does_not_clear_another_requests_uncertainty():
    budget = CallBudget(30000, 1000, 0, 0, 0)

    class RecordedCompletion:
        async def call(self, *args, **kwargs):
            budget.usage_uncertain = True  # Another in-flight request lost its response.
            raise response_error("protocol")

    with pytest.raises(ProviderResponseError):
        asyncio.run(BudgetedLLM(RecordedCompletion(), budget, 100).call("Known response"))
    assert budget.usage_uncertain


@pytest.mark.parametrize("kind", ["length", "protocol"])
def test_unverified_attached_usage_remains_unknown(kind):
    error = response_error(kind, usage_verified=False)

    class RecordedCompletion:
        async def call(self, *args, **kwargs):
            raise error

    budget = CallBudget(30000, 1000, 0, 0, 0)
    with pytest.raises(type(error)):
        asyncio.run(BudgetedLLM(RecordedCompletion(), budget, 100).call("Missing guard verification"))
    assert budget.usage_uncertain


def test_bad_routing_error_cannot_promote_itself_to_known_usage():
    error = FreeOnlyRoutingError("Unverified model or price")
    error.usage, error.usage_verified = SimpleNamespace(prompt_tokens=20, completion_tokens=4, cost_usd=0), True

    class RecordedCompletion:
        async def call(self, *args, **kwargs):
            raise error

    budget = CallBudget(30000, 1000, 0, 0, 0)
    with pytest.raises(FreeOnlyRoutingError):
        asyncio.run(BudgetedLLM(RecordedCompletion(), budget, 100).call("Rejected routing"))
    assert budget.usage_uncertain


@pytest.fixture
def free_policy(tmp_path):
    return recorded_policy.__wrapped__(tmp_path)


@pytest.fixture
def agent(free_policy, tmp_path, monkeypatch):
    yield from recorded_agent.__wrapped__(free_policy, tmp_path, monkeypatch)


@pytest.mark.parametrize("kind", ["length", "protocol", "routing"])
def test_free_query_propagates_one_error_without_upstream_hidden_retries(agent, kind):
    error = FreeOnlyRoutingError("Recorded guard stop") if kind == "routing" else response_error(kind)

    class RecordedChat:
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise error

    chat = RecordedChat()
    with pytest.raises(type(error)) as caught:
        agent._recording_test_loop.run_until_complete(agent._query_llm(chat, "One query"))
    assert caught.value is error and chat.calls == 1


@pytest.mark.parametrize("finish_reason,expected_type,expected_outcome", [
    ("length", "OutputLengthExceededError", "AGENT_TIMEOUT"),
    ("stop", "ProviderResponseError", "UNSCORABLE"),
])
def test_pinned_run_retains_both_real_guard_receipts_before_nontext_failure(
    agent, monkeypatch, finish_reason, expected_type, expected_outcome,
):
    from harbor.models.agent.context import AgentContext

    supplied = [response(prompt_tokens=1345, completion_tokens=160),
                response(prompt_tokens=2093, completion_tokens=2048)]
    supplied[1]["choices"][0].update(finish_reason=finish_reason)
    supplied[1]["choices"][0]["message"].update(content=None, reasoning="Recorded reasoning; no usable action text")
    calls = []

    async def completed_request(**kwargs):
        calls.append(kwargs)
        return supplied[len(calls) - 1]

    agent._free_llm._completion = completed_request

    async def episode(*, initial_prompt, chat, original_instruction):
        await agent._query_llm(chat, "First recorded response")
        await agent._query_llm(chat, "Second recorded response")

    monkeypatch.setattr(agent, "_run_agent_loop", episode)
    context = AgentContext()
    with pytest.raises(Exception) as caught:
        agent._recording_test_loop.run_until_complete(agent.run("Offline fixture", SimpleNamespace(), context))
    assert type(caught.value).__name__ == expected_type
    assert len(calls) == 2 and agent._free_llm.requests == 2
    assert context.cost_usd == 0 and context.n_input_tokens == 3438 and context.n_output_tokens == 2208
    assert not context.metadata["budget_reservation"]["usage_uncertain"]
    assert context.metadata["free_only"]["usage_verified"] is True
    assert len(context.metadata["free_only"]["provider_usage"]) == 2
    assert context.metadata["free_only"]["last_failed_response_meta"]["finish_reason"] == finish_reason
    normalized = parse_trial_result({"agent_result": context.model_dump(), "exception_info": {
        "exception_type": type(caught.value).__name__, "exception_message": str(caught.value)}})
    assert normalized["outcome"] == expected_outcome and normalized["cost_usd"] == 0
    assert classify_exception({"exception_type": expected_type})[0] == expected_outcome
