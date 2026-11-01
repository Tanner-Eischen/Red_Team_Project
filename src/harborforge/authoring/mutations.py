"""Bounded, immutable discovery-only semantic task mutation."""
from __future__ import annotations

from pathlib import Path
from .provenance import read_manifest

MUTATIONS = {
    "scientific-etl": {"constraint-interaction": "Add timestamp groups while preserving explicit nullable status and revision ordering"},
    "geospatial-raster": {"grid-expansion": "Increase target grid extent to exercise CRS sampling, boundaries and NoData interactions"},
    "repository-debug": {"overlap-density": "Add overlapping and touching interval combinations under unchanged API contract"},
}


def revise_task(parent_path: Path | str, output_dir: Path | str, mutation: str | None = None,
                max_iterations: int = 3) -> Path:
    from harborforge.task_families.registry import generate_task
    parent = read_manifest(parent_path)
    if parent["cohort"] != "discovery":
        raise ValueError("Holdout/calibration tasks cannot enter adaptive task revision")
    if max_iterations < 0 or parent["version"] - 1 >= max_iterations:
        raise ValueError("Task lineage reached its revision limit")
    operations = MUTATIONS[parent["family"]]
    mutation = mutation or next(iter(operations))
    if mutation not in operations:
        raise ValueError(f"Unsupported mutation {mutation!r}; choose {', '.join(operations)}")
    return generate_task(parent["family"], parent["seed"], output_dir, cohort="discovery",
                         version=parent["version"] + 1, parent=parent,
                         mutation={"operator": mutation, "hypothesis": operations[mutation],
                                   "parent_artifact_sha256": parent["artifact_sha256"]})
