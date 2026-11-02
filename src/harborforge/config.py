"""Explicit, immutable experiment configuration; no provider defaults or credentials."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def config_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def openrouter_free_slug(model_id: str) -> str:
    """Only exact named free SKUs, never a generic or automatic router."""
    if not re.fullmatch(r"openrouter/[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*:free", model_id):
        raise ValueError("free_only requires a named canonical openrouter/<org>/<model>:free ID")
    slug = model_id.removeprefix("openrouter/")
    if slug.startswith("openrouter/"):
        raise ValueError("automatic OpenRouter routers are not permitted for free_only")
    return slug


class OpenRouterZeroPrices(Contract):
    prompt: float = Field(default=0.0, ge=0, le=0, strict=True)
    completion: float = Field(default=0.0, ge=0, le=0, strict=True)
    request: float = Field(default=0.0, ge=0, le=0, strict=True)
    image: float = Field(default=0.0, ge=0, le=0, strict=True)


class OpenRouterFreePolicy(Contract):
    """Pinned evidence and non-overridable zero-price transport requirements.

    This schema does no I/O and grants no execution authority. The host transport
    must verify evidence hashes, content, freshness and account constraints at send.
    """

    policy_version: Literal["openrouter-free-only-v1"] = "openrouter-free-only-v1"
    allowed_model_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    response_model_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    catalog_path: str = Field(min_length=1, max_length=4096)
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    endpoints_path: str = Field(min_length=1, max_length=4096)
    endpoints_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    account_evidence_path: str = Field(min_length=1, max_length=4096)
    account_evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_at_utc: str = Field(min_length=1)
    max_age_seconds: int = Field(default=3600, ge=60, le=3600, strict=True)
    max_requests_per_attempt: int = Field(default=12, ge=1, le=16, strict=True)
    minimum_request_interval_seconds: float = Field(default=4.0, ge=4, le=60, strict=True)
    provider_max_price: OpenRouterZeroPrices = Field(default_factory=OpenRouterZeroPrices)
    allow_fallbacks: Literal[False] = False
    allow_paid_fallback: Literal[False] = False
    max_usd_per_request: float = Field(default=0.0, ge=0, le=0, strict=True)

    @field_validator("catalog_path", "endpoints_path", "account_evidence_path")
    @classmethod
    def local_json_evidence(cls, value: str) -> str:
        if "\x00" in value or "://" in value or Path(value).suffix.lower() != ".json":
            raise ValueError("free routing evidence must be a local JSON path, never a URL or credential")
        return value

    @model_validator(mode="after")
    def named_and_dated(self) -> OpenRouterFreePolicy:
        for value in self.allowed_model_ids:
            openrouter_free_slug(value)
        if len(set(self.allowed_model_ids)) != len(self.allowed_model_ids):
            raise ValueError("duplicate free model allowlist IDs")
        for alias in self.response_model_ids:
            if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*(?::free)?", alias)
                    or alias.startswith("openrouter/")):
                raise ValueError("response model aliases must be exact named raw OpenRouter model IDs")
        if len(set(self.response_model_ids)) != len(self.response_model_ids):
            raise ValueError("duplicate response model aliases")
        try:
            moment = datetime.fromisoformat(self.verified_at_utc.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("verified_at_utc must be an aware ISO UTC timestamp") from error
        if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
            raise ValueError("verified_at_utc must be an aware ISO UTC timestamp")
        return self


class ModelProfile(Contract):
    profile_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    snapshot: str | None = None
    tier: int = Field(default=1, ge=1, le=3)
    repeats: int = Field(default=1, ge=1, le=20)
    supports_tools: bool = True
    max_cost_usd_per_attempt: float = Field(ge=0)
    billing_mode: Literal["standard", "free_only"] = "standard"
    free_routing: OpenRouterFreePolicy | None = None
    max_input_tokens: int = Field(default=32000, gt=0)
    max_output_tokens: int = Field(default=8000, gt=0)
    input_usd_per_million: float = Field(ge=0)
    output_usd_per_million: float = Field(ge=0)
    price_source: str = Field(min_length=1)
    price_checked_utc: str = Field(min_length=1)
    agent_kwargs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile_id", "provider", "model_id", "family")
    @classmethod
    def no_placeholders(cls, value: str) -> str:
        if not value.strip() or any(s in value.upper() for s in ("REPLACE_", "PIN_", "TBD", "PLACEHOLDER")):
            raise ValueError("replace placeholder identifiers before running")
        return value

    @field_validator("max_cost_usd_per_attempt", "input_usd_per_million", "output_usd_per_million", mode="before")
    @classmethod
    def monetary_values_are_not_flags(cls, value):
        if isinstance(value, bool):
            raise ValueError("monetary amounts must be numbers, not boolean flags")
        return value

    @model_validator(mode="after")
    def cost_envelope(self) -> ModelProfile:
        if self.billing_mode == "free_only":
            if self.provider != "openrouter" or self.free_routing is None:
                raise ValueError("free_only requires OpenRouter and an explicit verified free routing policy")
            requested = openrouter_free_slug(self.model_id)
            if self.model_id not in self.free_routing.allowed_model_ids:
                raise ValueError("free model must be in its exact allowed_model_ids")
            if requested not in self.free_routing.response_model_ids:
                raise ValueError("free response aliases must include the exact requested raw model ID")
            if any(value != 0 for value in (self.max_cost_usd_per_attempt,
                                          self.input_usd_per_million, self.output_usd_per_million)):
                raise ValueError("free_only requires exactly zero attempt cap and all token prices")
        elif self.max_cost_usd_per_attempt == 0 or self.free_routing is not None:
            raise ValueError("standard billing requires a positive reservation and no free routing policy")
        ceiling = (self.max_input_tokens * self.input_usd_per_million
                   + self.max_output_tokens * self.output_usd_per_million) / 1_000_000
        if self.max_cost_usd_per_attempt + 1e-12 < ceiling:
            raise ValueError("per-attempt reservation must cover configured token/price envelope")
        if any("key" in key.lower() or "secret" in key.lower() for key in self.agent_kwargs):
            raise ValueError("credentials must remain outside campaign configs and logs")
        if set(self.agent_kwargs) - {"temperature", "reasoning_effort"}:
            raise ValueError("model agent_kwargs may only set declared sampling/reasoning controls; harness must match")
        return self


class HarnessProfile(Contract):
    profile_id: str = "harbor-terminus-0.13.2"
    protocol: Literal["terminal_agent"] = "terminal_agent"
    harbor_version: Literal["0.13.2"] = "0.13.2"
    agent: Literal["terminus-2"] = "terminus-2"
    timeout_seconds: int = Field(default=600, gt=0)
    verifier_timeout_seconds: int = Field(default=120, gt=0)
    trial_time_limit_sec: int = Field(default=1200, gt=0)
    max_input_tokens: int = Field(default=32000, gt=0)
    max_output_tokens: int = Field(default=8000, gt=0)
    per_call_output_tokens: int = Field(default=2048, gt=0, strict=True)
    network_mode: Literal["no-network"] = "no-network"
    system_prompt_sha256: str | None = None
    tool_schema_sha256: str | None = None

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_output_cap(cls, value):
        # The former adapter used min(2048, cumulative output). Preserve that
        # behavior when an old configuration omits this newly declared field.
        if isinstance(value, dict) and "per_call_output_tokens" not in value:
            try:
                total = int(value.get("max_output_tokens", 8000))
            except (TypeError, ValueError, OverflowError):
                return value  # Normal field validation reports invalid totals.
            if total > 0:
                value = {**value, "per_call_output_tokens": min(2048, total)}
        return value

    @model_validator(mode="after")
    def per_call_within_cumulative_output(self) -> HarnessProfile:
        if self.per_call_output_tokens > self.max_output_tokens:
            raise ValueError("per_call_output_tokens cannot exceed the cumulative max_output_tokens")
        return self


class Budgets(Contract):
    campaign_usd_max: float = Field(default=0, ge=0)
    total_input_tokens_max: int = Field(default=0, ge=0)
    total_output_tokens_max: int = Field(default=0, ge=0)
    wall_time_seconds_max: int = Field(default=3600, gt=0)
    max_concurrency: int = Field(default=1, ge=1, le=8)
    max_model_attempts: int = Field(default=0, ge=0)
    retry_provider_errors: int = Field(default=0, ge=0, le=3)
    max_provider_retries_total: int | None = Field(default=None, ge=0, le=10000)
    max_task_revisions: int = Field(default=0, ge=0, le=10)


class Promotion(Contract):
    minimum_model_failures: int = Field(default=1, ge=1)
    require_human_review_for_tier3: bool = True
    human_reviewed_task_ids: tuple[str, ...] = ()


class DiscoveryPolicy(Contract):
    """Schema-2-only opt-in; schema-1 revision semantics remain unchanged."""

    policy_version: Literal["bounded-discovery-v1"] = "bounded-discovery-v1"
    max_revisions_per_lineage: int = Field(default=0, ge=0, le=10)
    max_revisions_total: int = Field(default=0, ge=0, le=100)
    max_generation_attempts: int = Field(default=0, ge=0, le=100)
    max_no_progress: int = Field(default=1, ge=1, le=10)
    max_attempts_per_tier: int = Field(default=12, ge=1, le=10000)
    exploration_quota: int = Field(default=0, ge=0, le=100)
    all_pass_operator_ids: tuple[str, ...] = ()
    reviewed_finding_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def supported_operators(self) -> DiscoveryPolicy:
        from harborforge.authoring.mutations import CATALOG

        if set(self.all_pass_operator_ids) - set(CATALOG):
            raise ValueError("all-pass escalation requires supported predeclared operator IDs")
        if len(set(self.all_pass_operator_ids)) != len(self.all_pass_operator_ids):
            raise ValueError("duplicate all-pass operator IDs")
        if len(set(self.reviewed_finding_ids)) != len(self.reviewed_finding_ids):
            raise ValueError("duplicate reviewed finding IDs")
        return self


class CampaignConfig(Contract):
    schema_version: Literal["1", "2"] = "1"
    campaign_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    objective: Literal["adversarial_discovery", "fixed_evaluation"] = "adversarial_discovery"
    cohort: Literal["discovery", "calibration", "holdout"] = "discovery"
    protocol: Literal["terminal_agent"] = "terminal_agent"
    tasks: tuple[str, ...] = ()
    suite_manifest: str | None = None
    harness_profile: HarnessProfile = Field(default_factory=HarnessProfile)
    model_profiles: tuple[ModelProfile, ...] = ()
    budgets: Budgets = Field(default_factory=Budgets)
    promotion: Promotion = Field(default_factory=Promotion)
    discovery_policy: DiscoveryPolicy | None = None
    include_timeouts_in_denominator: bool = False

    @model_validator(mode="after")
    def experimental_boundaries(self) -> CampaignConfig:
        if self.schema_version == "1" and self.discovery_policy is not None:
            raise ValueError("discovery_policy requires explicit schema_version=2 migration")
        if self.schema_version == "2" and self.objective == "adversarial_discovery":
            if self.discovery_policy is None:
                raise ValueError("schema 2 discovery requires an explicit discovery_policy")
            if self.budgets.max_task_revisions:
                raise ValueError("schema 2 uses separate revision limits; legacy max_task_revisions must be zero")
        if self.objective == "fixed_evaluation" and self.discovery_policy is not None:
            raise ValueError("fixed evaluation cannot install an adaptive discovery policy")
        if self.objective == "adversarial_discovery" and self.cohort != "discovery":
            raise ValueError("adaptive discovery can only read discovery tasks")
        if self.objective == "fixed_evaluation" and not self.suite_manifest:
            raise ValueError("fixed evaluation requires a frozen, preregistered suite")
        if self.objective == "fixed_evaluation" and self.budgets.max_task_revisions:
            raise ValueError("fixed evaluation cannot revise tasks")
        if len({m.profile_id for m in self.model_profiles}) != len(self.model_profiles):
            raise ValueError("duplicate model profile IDs")
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError("duplicate task paths")
        if any(model.billing_mode == "free_only" for model in self.model_profiles):
            if any(model.billing_mode != "free_only" for model in self.model_profiles):
                raise ValueError("free_only campaigns cannot mix standard or paid model profiles")
            if self.budgets.campaign_usd_max != 0:
                raise ValueError("free_only campaign dollar ceiling must be exactly zero")
            if self.budgets.retry_provider_errors != 0 or self.budgets.max_provider_retries_total != 0:
                raise ValueError("free_only campaigns require explicit zero automatic provider retries")
        for model in self.model_profiles:
            if not model.supports_tools:
                raise ValueError("terminal_agent requires tool-capable models")
            if model.max_input_tokens != self.harness_profile.max_input_tokens or \
                    model.max_output_tokens != self.harness_profile.max_output_tokens:
                raise ValueError("compared models must share harness token limits")
        return self


def load_campaign(path: Path | str) -> CampaignConfig:
    path = Path(path)
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("campaign must be a mapping")
    # Paths in a configuration resolve relative to its directory, never process cwd.
    value["tasks"] = [str((path.parent / p).resolve()) for p in value.get("tasks", [])]
    if value.get("suite_manifest"):
        value["suite_manifest"] = str((path.parent / value["suite_manifest"]).resolve())
    for profile in value.get("model_profiles", []):
        policy = profile.get("free_routing") if isinstance(profile, dict) else None
        if isinstance(policy, dict):
            for key in ("catalog_path", "endpoints_path", "account_evidence_path"):
                if key in policy:
                    policy[key] = str((path.parent / policy[key]).resolve())
    return CampaignConfig.model_validate(value)
