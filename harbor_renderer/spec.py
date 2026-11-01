from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PackageSpec(BaseModel):
    name: str
    version: str

    model_config = {"extra": "forbid"}


class EnvironmentSpec(BaseModel):
    base_image: str = "ubuntu:22.04"
    apt_packages: list[PackageSpec] = Field(default_factory=list)
    pip_packages: list[PackageSpec] = Field(default_factory=list)
    workdir: str = "/"

    model_config = {"extra": "forbid"}


class IOSpec(BaseModel):
    path: str
    description: str

    model_config = {"extra": "forbid"}

    @field_validator("path")
    @classmethod
    def validate_absolute_posix(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.startswith("/"):
            raise ValueError("path must be an absolute POSIX path (start with '/')")
        return v


class OracleStrategy(BaseModel):
    steps: list[str]
    verifier_python: str

    model_config = {"extra": "forbid"}


class TestCaseSpec(BaseModel):
    id: str
    requirement_ids: list[str]
    input_path: str
    description: str

    model_config = {"extra": "forbid"}

    @field_validator("input_path")
    @classmethod
    def validate_absolute_posix(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.startswith("/"):
            raise ValueError("input_path must be an absolute POSIX path (start with '/')")
        return v


class TestPlanSpec(BaseModel):
    requirements: list[str]
    cases: list[TestCaseSpec]

    model_config = {"extra": "forbid"}


class AntiCheatingSpec(BaseModel):
    constraints: list[str]

    model_config = {"extra": "forbid"}


class MetadataSpec(BaseModel):
    category: str
    difficulty: Literal["easy", "medium", "hard", "very_hard"]
    tags: list[str] = Field(default_factory=list)
    parent_task_id: str | None = None
    created: str = Field(default_factory=lambda: datetime.now().isoformat())

    model_config = {"extra": "forbid"}


class TaskSpec(BaseModel):
    task_id: str
    title: str
    instruction_md: str
    metadata: MetadataSpec
    inputs: list[IOSpec]
    outputs: list[IOSpec]
    reward_path: str
    environment: EnvironmentSpec
    oracle: OracleStrategy
    test_plan: TestPlanSpec
    anti_cheating: AntiCheatingSpec
    # Older specs omit independent fixtures and must not be scored as valid.
    public_files: dict[str, str] = Field(default_factory=dict)
    expected_outputs: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("reward_path")
    @classmethod
    def validate_reward_path(cls, v: str) -> str:
        v = (v or "").strip()
        if not v.startswith("/"):
            raise ValueError("reward_path must be an absolute POSIX path (start with '/')")
        return v

    def to_metadata_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "category": self.metadata.category,
            "difficulty": self.metadata.difficulty,
            "tags": list(self.metadata.tags),
            "parent_task_id": self.metadata.parent_task_id,
            "created": self.metadata.created,
            "inputs": [x.model_dump(mode="json") for x in self.inputs],
            "outputs": [x.model_dump(mode="json") for x in self.outputs],
            "reward_path": self.reward_path,
            "oracle_steps": list(self.oracle.steps),
            "anti_cheating": list(self.anti_cheating.constraints),
            "test_requirements": list(self.test_plan.requirements),
            "test_cases": [c.model_dump(mode="json") for c in self.test_plan.cases],
        }

    @field_validator("public_files")
    @classmethod
    def validate_public_files(cls, files: dict[str, str]) -> dict[str, str]:
        for name in files:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
                raise ValueError("public file names must be safe relative POSIX paths under /app")
        return files

