"""Conservative reservations: unknown usage keeps the whole reservation spent."""

from __future__ import annotations

import time
from dataclasses import dataclass

from harborforge.config import Budgets, ModelProfile


class BudgetExceeded(ValueError):
    pass


@dataclass(frozen=True)
class Reservation:
    cost: float
    input_tokens: int
    output_tokens: int


class BudgetLedger:
    def __init__(self, limits: Budgets):
        self.limits = limits
        self.started = time.monotonic()
        self.cost = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.attempts = 0
        self.uncertain = False

    def reserve(self, model: ModelProfile, timeout_seconds: int) -> Reservation:
        if self.uncertain:
            raise BudgetExceeded("previous attempt has unknown usage; reconcile before further calls")
        if time.monotonic() - self.started + timeout_seconds > self.limits.wall_time_seconds_max:
            raise BudgetExceeded("wall-time budget cannot cover another full attempt")
        if self.attempts >= self.limits.max_model_attempts:
            raise BudgetExceeded("model-attempt budget exhausted")
        reservation = Reservation(model.max_cost_usd_per_attempt, model.max_input_tokens, model.max_output_tokens)
        if self.cost + reservation.cost > self.limits.campaign_usd_max + 1e-12:
            raise BudgetExceeded("dollar budget cannot cover next reservation")
        if self.input_tokens + reservation.input_tokens > self.limits.total_input_tokens_max:
            raise BudgetExceeded("input-token budget cannot cover next reservation")
        if self.output_tokens + reservation.output_tokens > self.limits.total_output_tokens_max:
            raise BudgetExceeded("output-token budget cannot cover next reservation")
        self.cost += reservation.cost
        self.input_tokens += reservation.input_tokens
        self.output_tokens += reservation.output_tokens
        self.attempts += 1
        return reservation

    def settle(self, reserved: Reservation, result: dict) -> None:
        values = [result.get("cost_usd"), result.get("tokens_input"), result.get("tokens_output")]
        if any(value is None for value in values):
            self.uncertain = True
            return
        cost, tokens_in, tokens_out = values
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
                   and value < float("inf") for value in values):
            self.uncertain = True
            return
        if cost > reserved.cost + 1e-12 or tokens_in > reserved.input_tokens or tokens_out > reserved.output_tokens:
            self.uncertain = True
            # Actual usage remains visible even when it exceeds the configured estimate.
            self.cost += max(0, cost - reserved.cost)
            self.input_tokens += max(0, tokens_in - reserved.input_tokens)
            self.output_tokens += max(0, tokens_out - reserved.output_tokens)
            return
        self.cost -= reserved.cost - cost
        self.input_tokens -= reserved.input_tokens - tokens_in
        self.output_tokens -= reserved.output_tokens - tokens_out

    def snapshot(self) -> dict:
        return {"reserved_or_measured_cost_usd": self.cost, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "attempts": self.attempts,
                "usage_uncertain": self.uncertain}
