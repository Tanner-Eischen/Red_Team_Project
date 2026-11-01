"""Read-only arithmetic for a proposed pilot; this module cannot execute an agent."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from harborforge.config import CampaignConfig, Contract


class PilotProposal(Contract):
    proposal_id: str = Field(min_length=1)
    approval_status: Literal["proposed_not_authorized"]
    task_families: tuple[Literal["scientific-etl", "geospatial-raster", "repository-debug"], ...]
    provider_retry_allowance: int = Field(ge=0, le=2)
    billing_scope: Literal["standard_global_text_only_uncached"]
    campaign: CampaignConfig

    @model_validator(mode="after")
    def pilot_only(self):
        if not self.task_families or len(set(self.task_families)) != len(self.task_families):
            raise ValueError("pilot needs distinct task families")
        if not self.campaign.model_profiles:
            raise ValueError("proposal needs explicit model profiles")
        if self.campaign.tasks or self.campaign.suite_manifest:
            raise ValueError("proposal is not an executable campaign or a frozen evaluation")
        if self.campaign.budgets.max_task_revisions:
            raise ValueError("pilot proposal cannot silently include adaptive revisions")
        if self.campaign.discovery_policy is not None:
            raise ValueError("pilot proposal cannot include adaptive discovery policy")
        if len({model.tier for model in self.campaign.model_profiles}) != 1:
            raise ValueError("all pilot profiles must share one tier to retain the complete planned matrix")
        if self.campaign.budgets.max_provider_retries_total != self.provider_retry_allowance:
            raise ValueError("global provider-retry ceiling must match the proposed allowance")
        if self.campaign.harness_profile.max_input_tokens > 256_000:
            raise ValueError("this short-context proposal requires an input envelope at most 256000")
        return self


def estimate_proposal(value: dict) -> dict:
    """Validate declared envelopes, without imports of providers, runners or registries."""
    proposal = PilotProposal.model_validate(value)
    config = proposal.campaign
    task_count = len(proposal.task_families)
    rows = []
    for model in config.model_profiles:
        input_rate = Decimal(str(model.input_usd_per_million))
        output_rate = Decimal(str(model.output_usd_per_million))
        token_ceiling = (model.max_input_tokens * input_rate + model.max_output_tokens * output_rate) / 1_000_000
        rows.append({
            "model_id": model.model_id,
            "planned_attempts": task_count * model.repeats,
            "configured_token_ceiling_usd_per_attempt": float(token_ceiling),
            "reserved_usd_per_attempt": model.max_cost_usd_per_attempt,
        })
    attempts = sum(row["planned_attempts"] for row in rows)
    retry_reserve = proposal.provider_retry_allowance * max(
        Decimal(str(model.max_cost_usd_per_attempt)) for model in config.model_profiles
    )
    primary_reserve = sum(Decimal(str(row["reserved_usd_per_attempt"])) * row["planned_attempts"] for row in rows)
    token_cost = sum(
        Decimal(str(row["configured_token_ceiling_usd_per_attempt"])) * row["planned_attempts"] for row in rows
    )
    required_attempts = attempts + proposal.provider_retry_allowance
    limits = config.budgets
    if limits.max_model_attempts != required_attempts:
        raise ValueError("attempt ceiling must match planned attempts plus bounded provider retries")
    if primary_reserve + retry_reserve > Decimal(str(limits.campaign_usd_max)):
        raise ValueError("campaign cap cannot cover primary and retry reservations")
    if limits.total_input_tokens_max < required_attempts * config.harness_profile.max_input_tokens:
        raise ValueError("campaign input envelope cannot cover all planned reservations")
    if limits.total_output_tokens_max < required_attempts * config.harness_profile.max_output_tokens:
        raise ValueError("campaign output envelope cannot cover all planned reservations")
    if proposal.provider_retry_allowance and not limits.retry_provider_errors:
        raise ValueError("retry allowance requires an explicit provider-error retry policy")
    return {
        "proposal_id": proposal.proposal_id,
        "approval_status": proposal.approval_status,
        "model_run_authorized": False,
        "model_requests_executed": 0,
        "pricing_basis": "Declared dated prices; no account access or provider availability check",
        "task_count": task_count,
        "models": rows,
        "planned_attempts": attempts,
        "provider_retry_allowance": proposal.provider_retry_allowance,
        "maximum_total_attempts": required_attempts,
        "primary_token_envelope_usd": float(token_cost),
        "primary_reservations_usd": float(primary_reserve),
        "retry_reservations_usd": float(retry_reserve),
        "total_reservations_usd": float(primary_reserve + retry_reserve),
        "campaign_hard_cap_usd": limits.campaign_usd_max,
        "limits": [
            "Conservative configured-price envelopes are not measured provider bills or a billing guarantee.",
            "Unknown usage stops execution for reconciliation; model failures are not retried.",
            "Parent/child confirmation and frozen-suite measurement need separate proposals.",
            "This proposal must be approved and concrete task/model/harness artifacts pinned before execution.",
        ],
    }
