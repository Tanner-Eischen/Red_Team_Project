"""Conservative pre-call reservations, including failed or truncated calls."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from contextvars import ContextVar
from typing import Any

ACTIVE_REQUEST_RESERVATION: ContextVar[dict | None] = ContextVar("harborforge_request_reservation", default=None)


class AgentBudgetExceededError(Exception):
    """The agent has exhausted a declared token or configured-price envelope."""


class ProviderUsageError(AgentBudgetExceededError):
    """Provider accounting is unresolved, rather than a task-capability failure."""


class ProviderResponseError(Exception):
    """A completed, accounted response violated the text/protocol contract."""

    def __init__(self, message: str, *, usage: Any = None, usage_verified: bool = False):
        super().__init__(message)
        self.usage = usage
        self.usage_verified = usage_verified


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
            raise ProviderUsageError("Previous provider usage requires reconciliation before another model call")
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

    def _validate_usage(self, usage: Any, input_allowance: int, output_allowance: int, cost_allowance: float) -> None:
        tokens_in = getattr(usage, "prompt_tokens", None)
        tokens_out = getattr(usage, "completion_tokens", None)
        cost = getattr(usage, "cost_usd", None)
        if (type(tokens_in) is not int or tokens_in < 0 or
                type(tokens_out) is not int or tokens_out < 0 or
                isinstance(cost, bool) or not isinstance(cost, (int, float)) or
                not 0 <= cost <= sys.float_info.max):
            self.budget.usage_uncertain = True
            raise ProviderUsageError("Provider usage is missing or invalid; reconciliation required")
        if (tokens_in > input_allowance or tokens_out > output_allowance
                or cost > cost_allowance + (0 if self.budget.max_cost_usd == 0 else 1e-12)):
            self.budget.usage_uncertain = True
            raise ProviderUsageError("Provider-reported usage exceeded the configured reservation")

    async def call(self, prompt: str, **kwargs: Any) -> Any:
        prepare = getattr(self.inner, "prepare_budgeted_call", None)
        if prepare is not None:
            prompt, kwargs = prepare(prompt, kwargs)
        kwargs["max_tokens"] = min(int(kwargs.get("max_tokens", self.per_call_output)), self.per_call_output)
        kwargs["num_retries"] = 0
        input_before, cost_before = self.budget.input_reserved, self.budget.cost_reserved
        kwargs["max_tokens"] = self.budget.reserve(prompt, kwargs)
        # Capture this request's envelope before awaiting: another in-flight
        # request must not enlarge the allowance used to validate this response.
        input_allowance = self.budget.input_reserved - input_before
        cost_allowance = self.budget.cost_reserved - cost_before
        # Harbor 0.13.2 adds tenacity retries around LiteLLM.call. Each retry
        # would need its own reservation; use its undecorated method instead.
        reservation_token = ACTIVE_REQUEST_RESERVATION.set({"input": input_allowance,
            "output": kwargs["max_tokens"], "cost": cost_allowance})
        try:
            unwrapped = getattr(self.inner.call, "__wrapped__", None)
            if unwrapped is not None:
                response = await unwrapped(self.inner, prompt, **kwargs)
            else:
                response = await self.inner.call(prompt, **kwargs)
        except BaseException as error:
            known_response_type = isinstance(error, ProviderResponseError) or (
                type(error).__module__ == "harbor.llms.base" and type(error).__name__ == "OutputLengthExceededError"
            )
            if known_response_type and getattr(error, "usage_verified", None) is True:
                # The host guard completed the response and checked its identity
                # before this protocol/length error. Recheck its actual usage
                # against this exact reservation, without refunding or clearing
                # uncertainty left by any other request.
                self._validate_usage(getattr(error, "usage", None), input_allowance, kwargs["max_tokens"], cost_allowance)
            else:
                # A provider may bill a request even when its response is lost.
                self.budget.usage_uncertain = True
            raise
        finally:
            ACTIVE_REQUEST_RESERVATION.reset(reservation_token)
        usage = getattr(response, "usage", None)
        if usage is None:
            self.budget.usage_uncertain = True
        else:
            self._validate_usage(usage, input_allowance, kwargs["max_tokens"], cost_allowance)
        return response
