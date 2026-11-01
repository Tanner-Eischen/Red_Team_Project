"""Conservative pre-call reservations, including failed or truncated calls."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


class AgentBudgetExceededError(Exception):
    """The agent has exhausted a declared token or configured-price envelope."""


@dataclass
class CallBudget:
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: float
    input_usd_per_million: float
    output_usd_per_million: float
    input_reserved: int = 0
    output_reserved: int = 0
    cost_reserved: float = 0.0
    usage_uncertain: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in (self.max_input_tokens, self.max_output_tokens)):
            raise ValueError("token envelopes must be positive integers")
        if any(not math.isfinite(value) or value < 0 for value in
               (self.max_cost_usd, self.input_usd_per_million, self.output_usd_per_million)):
            raise ValueError("price and cost envelopes must be finite nonnegative numbers")

    def reserve(self, prompt: str, kwargs: dict[str, Any]) -> int:
        if self.usage_uncertain:
            raise AgentBudgetExceededError("Previous provider usage requires reconciliation before another model call")
        # A byte-per-token upper envelope deliberately overestimates normal
        # byte-level model tokenizers. Additional headroom covers message framing.
        # Tool schemas and full history must be included, not just the last turn.
        def serialize(value: Any) -> Any:
            if isinstance(value, type) and hasattr(value, "model_json_schema"):
                return value.model_json_schema()
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            return str(value)

        encoded = json.dumps({"prompt": prompt, "kwargs": kwargs}, ensure_ascii=False, default=serialize).encode("utf-8")
        if any(term in encoded for term in (b'"image_url"', b'"input_image"', b'"audio_url"', b'"input_audio"')):
            raise AgentBudgetExceededError("Multimodal requests have no verified token envelope")
        input_upper = len(encoded) + 4096
        available_output = self.max_output_tokens - self.output_reserved
        if self.input_reserved + input_upper > self.max_input_tokens or available_output <= 0:
            raise AgentBudgetExceededError("Cumulative token envelope exhausted before model call")
        output_limit = min(int(kwargs.get("max_tokens", available_output)), available_output)
        if output_limit <= 0:
            raise AgentBudgetExceededError("Output reservation must be positive")
        reserve_cost = (input_upper * self.input_usd_per_million + output_limit * self.output_usd_per_million) / 1e6
        if self.cost_reserved + reserve_cost > self.max_cost_usd:
            raise AgentBudgetExceededError("Configured-price cost envelope exhausted before model call")
        self.input_reserved += input_upper
        self.output_reserved += output_limit
        self.cost_reserved += reserve_cost
        return output_limit


class BudgetedLLM:
    def __init__(self, inner: Any, budget: CallBudget, per_call_output: int) -> None:
        self.inner, self.budget, self.per_call_output = inner, budget, per_call_output

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def call(self, prompt: str, **kwargs: Any) -> Any:
        kwargs["max_tokens"] = min(int(kwargs.get("max_tokens", self.per_call_output)), self.per_call_output)
        kwargs["num_retries"] = 0
        input_before, cost_before = self.budget.input_reserved, self.budget.cost_reserved
        kwargs["max_tokens"] = self.budget.reserve(prompt, kwargs)
        # Harbor 0.13.2 adds tenacity retries around LiteLLM.call. Each retry
        # would need its own reservation; use its undecorated method instead.
        try:
            unwrapped = getattr(self.inner.call, "__wrapped__", None)
            if unwrapped is not None:
                response = await unwrapped(self.inner, prompt, **kwargs)
            else:
                response = await self.inner.call(prompt, **kwargs)
        except BaseException:
            # A provider may bill a request even when its response is lost.
            self.budget.usage_uncertain = True
            raise
        usage = getattr(response, "usage", None)
        if usage is None:
            self.budget.usage_uncertain = True
        else:
            if (usage.prompt_tokens > self.budget.input_reserved - input_before
                    or usage.completion_tokens > kwargs["max_tokens"]
                    or usage.cost_usd > self.budget.cost_reserved - cost_before + 1e-12):
                self.budget.usage_uncertain = True
                raise AgentBudgetExceededError("Provider-reported usage exceeded the configured reservation")
        return response
