"""Explicit, immutable experiment configuration; no provider defaults or credentials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def config_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ModelProfile(Contract):
    profile_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    snapshot: str | None = None
    tier: int = Field(default=1, ge=1, le=3)
    repeats: int = Field(default=1, ge=1, le=20)
    supports_tools: bool = True
    max_cost_usd_per_attempt: float = Field(gt=0)
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

    @model_validator(mode="after")
    def cost_envelope(self) -> ModelProfile:
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
    network_mode: Literal["no-network"] = "no-network"
    system_prompt_sha256: str | None = None
    tool_schema_sha256: str | None = None


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
    return CampaignConfig.model_validate(value)
