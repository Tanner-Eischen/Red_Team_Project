"""Bounded, immutable discovery-only semantic task mutation."""
from __future__ import annotations

from pathlib import Path
import hashlib
import shutil
from .provenance import read_manifest


class NoSemanticChange(ValueError):
    """A proposal changed only lineage metadata, not actual scored task inputs."""


def semantic_identity(task_path: Path | str) -> tuple:
    task = Path(task_path)
    inputs = task / "environment" / "public" / "input"
    public = tuple((path.relative_to(inputs).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                   for path in sorted(inputs.rglob("*")) if path.is_file())
    return public, hashlib.sha256((task / "tests" / "cases.json").read_bytes()).hexdigest()

CATALOG = {
    "etl-nullable-revisions": {"family": "scientific-etl", "categories": ("parsing_or_schema", "behavioral_assertion"),
        "skill": "select newest revision before nullable-status filtering",
        "negative_control": "accept blank status", "dimension": "nullable-revision interaction"},
    "etl-mixed-unit-ordering": {"family": "scientific-etl", "categories": ("numerical_or_units",),
        "skill": "convert mixed units before stable aggregation",
        "negative_control": "reverse Kelvin conversion", "dimension": "mixed-unit/revision ordering"},
    "raster-grid-shift-nodata": {"family": "geospatial-raster", "categories": ("parsing_or_schema", "behavioral_assertion"),
        "skill": "align shifted grids without resurrecting NoData",
        "negative_control": "treat source NoData as valid", "dimension": "grid shift and NoData boundary"},
    "raster-non-square-spacing": {"family": "geospatial-raster", "categories": ("numerical_or_units",),
        "skill": "respect non-square target spacing and nearest-neighbor centers",
        "negative_control": "use bilinear resampling", "dimension": "non-square spacing"},
    "repository-boundary-overlap": {"family": "repository-debug", "categories": ("behavioral_assertion",),
        "skill": "merge touching intervals while discarding empties",
        "negative_control": "keep touching intervals separate", "dimension": "boundary overlap interaction"},
    "repository-input-ordering": {"family": "repository-debug", "categories": ("parsing_or_schema",),
        "skill": "return a sorted union independently of input order",
        "negative_control": "skip interval sorting", "dimension": "input ordering"},
}
for _operator in CATALOG.values():
    _operator.update(parameter_bounds={"level_min": 1, "level_max": 10},
        preconditions="discovery cohort; supported family; immutable parent",
        public_contract_delta="No new requirement: vary inputs within the existing public contract",
        independence_risk="Generated cases and controls need independent validation; no empirical difficulty claim")


def select_mutation(parent: dict, *, mode: str, all_pass_operator_ids=(), reviewed_evidence=()) -> dict | None:
    """Choose only preregistered dimensions or registry-confirmed reviewed evidence."""
    if parent["cohort"] != "discovery":
        raise ValueError("Mutation selection is discovery-only")
    eligible = {key: value for key, value in CATALOG.items() if value["family"] == parent["family"]}
    if mode == "all_pass":
        alternatives = [key for key in all_pass_operator_ids if key in eligible]
        evidence = []
        reason = "First eligible dimension in the preregistered all-pass operator order"
    elif mode == "reviewed_failure":
        evidence = [item for item in reviewed_evidence if item.get("decision") == "confirmed"
                    and item.get("reviewer") and item.get("finding_id") and item.get("trial_id")]
        alternatives = [key for key, spec in eligible.items()
                        if any(item.get("category") in spec["categories"] for item in evidence)]
        reason = "Eligible operators linked to append-only confirmed failure findings; hypothesis only"
    else:
        raise ValueError("Unknown mutation selection mode")
    if not alternatives:
        return None
    selected = alternatives[0]
    if mode == "reviewed_failure":
        evidence = [item for item in evidence if item.get("category") in eligible[selected]["categories"]]
    from .proposals import MutationProposal
    proposal = MutationProposal(operator_id=selected, family=parent["family"], level=parent["version"], mode=mode)
    return {"proposal": proposal.model_dump(), "catalog_version": "1", "operator_id": selected, "mode": mode, "reason": reason,
            "alternatives_considered": alternatives, "evidence": evidence,
            "hypothesis": eligible[selected]["skill"], "causal_claim": False, "difficulty_measured": False}


MUTATIONS = {
    "scientific-etl": {"constraint-interaction": "Add timestamp groups while preserving explicit nullable status and revision ordering"},
    "geospatial-raster": {"grid-expansion": "Increase target grid extent to exercise CRS sampling, boundaries and NoData interactions"},
    "repository-debug": {"overlap-density": "Add overlapping and touching interval combinations under unchanged API contract"},
}


def revise_task(parent_path: Path | str, output_dir: Path | str, mutation: str | None = None,
                max_iterations: int = 3, *, selection: dict | None = None) -> Path:
    from harborforge.task_families.registry import generate_task
    parent = read_manifest(parent_path)
    output_root = Path(output_dir).resolve()
    parent_root = Path(parent_path).resolve()
    if output_root.is_relative_to(parent_root):
        raise ValueError("Revision output cannot be inside its immutable parent")
    if parent["cohort"] != "discovery":
        raise ValueError("Holdout/calibration tasks cannot enter adaptive task revision")
    if max_iterations < 0 or parent["version"] - 1 >= max_iterations:
        raise ValueError("Task lineage reached its revision limit")
    operations = MUTATIONS[parent["family"]]
    mutation = mutation or next(iter(operations))
    catalog = CATALOG.get(mutation)
    if catalog and catalog["family"] != parent["family"]:
        raise ValueError("Mutation operator belongs to another task family")
    if mutation not in operations and catalog is None:
        raise ValueError(f"Unsupported mutation {mutation!r}; choose {', '.join(operations)}")
    details = {"operator": mutation, "hypothesis": catalog["skill"] if catalog else operations[mutation],
               "parent_artifact_sha256": parent["artifact_sha256"]}
    if catalog:
        from .proposals import MutationProposal
        MutationProposal(operator_id=mutation, family=parent["family"], level=parent["version"],
                         mode=selection["mode"] if selection else "manual")
        details.update(catalog_version="1", contract=catalog, selection=selection, difficulty_measured=False)
    child = generate_task(parent["family"], parent["seed"], output_root, cohort="discovery",
                         version=parent["version"] + 1, parent=parent,
                         mutation=details)
    if catalog and semantic_identity(child) == semantic_identity(parent_root):
        # generate_task succeeded only after its immutable destination preflight.
        # Remove only this newly created direct child, never any existing output.
        expected_name = f"{parent['task_id']}-v{parent['version'] + 1}"
        if child.is_symlink() or child.resolve().parent != output_root or child.name != expected_name:
            raise ValueError("Cannot safely discard semantic no-op proposal")
        shutil.rmtree(child)
        raise NoSemanticChange("Operator saturated: public input and private case bytes are unchanged")
    return child
