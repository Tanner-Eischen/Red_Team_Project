"""Conservative reservations: unknown usage keeps the whole reservation spent."""

from __future__ import annotations

import time
import sys
import threading
import uuid
from dataclasses import dataclass, field

from harborforge.config import Budgets, ModelProfile


class BudgetExceeded(ValueError):
    pass


class ReservationError(ValueError):
    """The handle was not issued here, changed, or was already settled."""


@dataclass(frozen=True)
class Reservation:
    cost: float
    input_tokens: int
    output_tokens: int
    reservation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class BudgetLedger:
    def __init__(self, limits: Budgets):
        self.limits = limits
        self.started = time.monotonic()
        self.cost = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.attempts = 0
        self.uncertain = False
        self._pending: dict[str, Reservation] = {}
        self._finalized: set[str] = set()
        self._lock = threading.RLock()

    def reserve(self, model: ModelProfile, timeout_seconds: int) -> Reservation:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("attempt timeout must be a positive integer")
        with self._lock:
            return self._reserve(model, timeout_seconds)

    def _reserve(self, model: ModelProfile, timeout_seconds: int) -> Reservation:
        if self.uncertain:
            raise BudgetExceeded("previous attempt has unknown usage; reconcile before further calls")
        if time.monotonic() - self.started + timeout_seconds > self.limits.wall_time_seconds_max:
            raise BudgetExceeded("wall-time budget cannot cover another full attempt")
        if self.attempts >= self.limits.max_model_attempts:
            raise BudgetExceeded("model-attempt budget exhausted")
        reservation = Reservation(model.max_cost_usd_per_attempt, model.max_input_tokens, model.max_output_tokens)
        if reservation.reservation_id in self._pending or reservation.reservation_id in self._finalized:
            raise ReservationError("reservation identifier was already issued")
        next_cost = self.cost + reservation.cost
        if ((self.limits.campaign_usd_max == 0 and next_cost > 0)
                or next_cost > self.limits.campaign_usd_max + 1e-12):
            raise BudgetExceeded("dollar budget cannot cover next reservation")
        if self.input_tokens + reservation.input_tokens > self.limits.total_input_tokens_max:
            raise BudgetExceeded("input-token budget cannot cover next reservation")
        if self.output_tokens + reservation.output_tokens > self.limits.total_output_tokens_max:
            raise BudgetExceeded("output-token budget cannot cover next reservation")
        self.cost += reservation.cost
        self.input_tokens += reservation.input_tokens
        self.output_tokens += reservation.output_tokens
        self.attempts += 1
        self._pending[reservation.reservation_id] = reservation
        return reservation

    def settle(self, reserved: Reservation, result: dict) -> None:
        with self._lock:
            self._settle(reserved, result)

    def _settle(self, reserved: Reservation, result: dict) -> None:
        if not isinstance(reserved, Reservation):
            raise ReservationError("settlement requires an issued reservation")
        if reserved.reservation_id in self._finalized:
            raise ReservationError("reservation was already settled; reconciliation is separate")
        issued = self._pending.get(reserved.reservation_id)
        if issued is None or issued != reserved:
            raise ReservationError("reservation was not issued by this ledger or was changed")
        # Unknown usage still consumes the original reservation. A later callback
        # cannot refund it by pretending to be the first settlement.
        del self._pending[issued.reservation_id]
        self._finalized.add(issued.reservation_id)
        reserved = issued
        if not isinstance(result, dict):
            self.uncertain = True
            return
        values = [result.get("cost_usd"), result.get("tokens_input"), result.get("tokens_output")]
        if any(value is None for value in values):
            self.uncertain = True
            return
        cost, tokens_in, tokens_out = values
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
                   and value <= sys.float_info.max for value in values):
            self.uncertain = True
            return
        if type(tokens_in) is not int or type(tokens_out) is not int:
            self.uncertain = True
            return
        if ((reserved.cost == 0 and cost > 0) or cost > reserved.cost + 1e-12
                or tokens_in > reserved.input_tokens or tokens_out > reserved.output_tokens):
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
        with self._lock:
            return {"reserved_or_measured_cost_usd": self.cost, "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens, "attempts": self.attempts,
                    "usage_uncertain": self.uncertain}
