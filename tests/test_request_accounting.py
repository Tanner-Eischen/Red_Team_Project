"""Recording-only provider witnesses; no network or model execution."""

import asyncio
from types import SimpleNamespace

import pytest

from harborforge.adapters.call_budget import BudgetedLLM, CallBudget, ProviderUsageError


@pytest.mark.parametrize("tokens_in,cost", [(7000, 0.0001), (100, 0.006)])
def test_concurrent_requests_cannot_borrow_another_calls_usage_envelope(tokens_in, cost):
    async def witness():
        second_started = asyncio.Event()
        calls = []

        class RecordingProvider:
            async def call(self, prompt, **kwargs):
                calls.append(prompt)
                if prompt == "first":
                    await second_started.wait()
                    usage = SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=10, cost_usd=cost)
                else:
                    second_started.set()
                    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, cost_usd=0.0001)
                return SimpleNamespace(usage=usage)

        budget = CallBudget(40000, 2000, 1, 1, 2)
        guarded = BudgetedLLM(RecordingProvider(), budget, 100)
        results = await asyncio.gather(guarded.call("first"), guarded.call("second"), return_exceptions=True)
        assert isinstance(results[0], ProviderUsageError)
        assert not isinstance(results[1], BaseException)
        assert budget.usage_uncertain
        with pytest.raises(ProviderUsageError):
            await guarded.call("third")
        assert calls == ["first", "second"]

    asyncio.run(witness())
