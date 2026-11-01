"""Typed author suggestions never contain executable code or dependency commands."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MutationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str
    family: str
    level: int = Field(ge=1, le=10, strict=True)
    mode: Literal["all_pass", "reviewed_failure", "manual"]

    @model_validator(mode="after")
    def permitted_operator(self):
        from harborforge.authoring.mutations import CATALOG

        specification = CATALOG.get(self.operator_id)
        if specification is None or specification["family"] != self.family:
            raise ValueError("Proposal must select a permitted operator for this family")
        return self
