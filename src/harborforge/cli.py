"""Explicit commands; displaying reports never opens a browser."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from harborforge.config import load_campaign


def _registry_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registry", type=Path, default=Path("artifacts/registry.sqlite"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="redforge", description="Validated adversarial discovery and fixed evaluation")
    sub = result.add_subparsers(dest="group", required=True)
    task = sub.add_parser("task", help="Author and validate immutable tasks").add_subparsers(dest="action", required=True)
    generate = task.add_parser("generate")
    generate.add_argument("--family", required=True)
    generate.add_argument("--seed", type=int, required=True)
    generate.add_argument("--count", type=int, default=1)
    generate.add_argument("--output-dir", type=Path, default=Path("tasks/generated"))
    generate.add_argument("--cohort", choices=["discovery", "calibration", "holdout"], default="discovery")
    validate = task.add_parser("validate")
    validate.add_argument("--task", type=Path, required=True)
    validate.add_argument("--runtime", choices=["local", "docker"], default="local")
    validate.add_argument("--evidence-dir", type=Path)
    revise = task.add_parser("revise")
    revise.add_argument("--task", type=Path, required=True)
    revise.add_argument("--output-dir", type=Path, default=Path("tasks/generated"))
    revise.add_argument("--mutation")
    revise.add_argument("--max-iterations", type=int, default=3)
    revise.add_argument("--from-trial", required=True, help="Recorded valid discovery MODEL_FAIL")
    _registry_option(revise)

    campaign = sub.add_parser("campaign", help="Budgeted discovery only").add_subparsers(dest="action", required=True)
    run = campaign.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-dir", type=Path)
    _registry_option(run)
    analyze = campaign.add_parser("analyze")
    analyze.add_argument("--campaign", required=True)
    _registry_option(analyze)
    budget = campaign.add_parser("budget", help="Calculate an unapproved pilot proposal without running models")
    budget.add_argument("--proposal", type=Path, required=True)

    evaluation = sub.add_parser("eval", help="Frozen independent evaluation").add_subparsers(dest="action", required=True)
    freeze = evaluation.add_parser("freeze")
    freeze.add_argument("--config", type=Path, required=True)
    freeze.add_argument("--output", type=Path)
    fixed = evaluation.add_parser("run")
    fixed.add_argument("--config", type=Path, required=True)
    fixed.add_argument("--output-dir", type=Path)
    _registry_option(fixed)

    report = sub.add_parser("report", help="Export static evidence without launching a UI").add_subparsers(
        dest="action", required=True)
    build = report.add_parser("build")
    build.add_argument("--campaign", required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--public", action="store_true", help="Reject private evaluation cohorts before export")
    _registry_option(build)
    review = sub.add_parser("review", help="Append a human adjudication; never overwrites findings")
    review.add_argument("--finding", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=["confirmed", "rejected", "uncertain"], required=True)
    review.add_argument("--notes", required=True)
    _registry_option(review)
    return result


def dispatch(args: argparse.Namespace) -> tuple[object, int]:
    if args.group == "campaign" and args.action == "budget":
        from harborforge.analysis.budget_proposal import estimate_proposal

        return estimate_proposal(json.loads(args.proposal.read_text(encoding="utf-8"))), 0
    if args.group == "task":
        if args.action == "generate":
            from harborforge.task_families.registry import generate_task

            if not 1 <= args.count <= 100:
                raise ValueError("count must be between 1 and 100")
            return {"tasks": [str(generate_task(args.family, args.seed + n, args.output_dir, cohort=args.cohort))
                              for n in range(args.count)]}, 0
        if args.action == "validate":
            from harborforge.validation.quality_gates import validate_task

            evidence = validate_task(args.task, runtime=args.runtime, evidence_dir=args.evidence_dir)
            return evidence, 0 if evidence.get("valid") else 1
        from harborforge.authoring.mutations import revise_task
        from harborforge.authoring.provenance import read_manifest
        from harborforge.storage.registry import Registry

        parent = read_manifest(args.task)
        with Registry(args.registry) as registry:
            row = registry.connection.execute("SELECT campaign_id FROM trials WHERE trial_id = ?", (args.from_trial,)).fetchone()
            if row is None:
                raise ValueError("revision requires recorded trial evidence")
            trial = next(t for t in registry.list_trials(row[0]) if t["trial_id"] == args.from_trial)
            if trial["outcome"] != "MODEL_FAIL" or trial["cohort"] != "discovery" or \
                    trial["task_version_id"] != parent["task_version_id"]:
                raise ValueError("revision evidence must be a valid MODEL_FAIL on this discovery parent")
            child = revise_task(args.task, args.output_dir, mutation=args.mutation, max_iterations=args.max_iterations)
            manifest = read_manifest(child)
            registry.register_task(manifest, child)
            registry.append_event(args.from_trial, "revision_proposed", {"child": manifest["task_version_id"],
                                                                        "mutation": manifest.get("mutation")})
        return {"task": str(child), "parent": parent["task_version_id"]}, 0

    if args.group == "eval" and args.action == "freeze":
        from harborforge.evaluation.frozen import freeze_suite, preregistration_config

        config = load_campaign(args.config)
        if config.objective != "fixed_evaluation":
            raise ValueError("freeze requires a fixed_evaluation configuration")
        return freeze_suite(list(config.tasks), args.output or config.suite_manifest,
                            preregistration_config(config.model_dump(mode="json"))), 0

    if args.group in {"campaign", "eval"} and args.action == "run":
        from harborforge.execution.campaign import run_campaign

        config = load_campaign(args.config)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output = args.output_dir or Path("artifacts/campaigns") / f"{config.campaign_id}-{stamp}"
        summary = run_campaign(config, args.registry, output, fixed=args.group == "eval")
        return summary, 0 if summary["status"] in {"complete", "validation_only"} else 1

    from harborforge.storage.registry import Registry

    with Registry(args.registry) as registry:
        if args.group == "campaign":
            from harborforge.analysis.failure_classifier import analyze_campaign

            return {"findings": analyze_campaign(registry, args.campaign)}, 0
        if args.group == "report":
            from harborforge.analysis.reporting import build_report

            return {"report": str(build_report(registry, args.campaign, args.out, public=args.public))}, 0
        if args.group == "review":
            return {"review_id": registry.review_finding(args.finding, args.reviewer, args.decision, args.notes)}, 0
    raise ValueError("unsupported command")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value, code = dispatch(args)
        print(json.dumps(value, indent=2, default=str, allow_nan=False))
        return code
    except (ValueError, OSError, KeyError) as error:
        print(f"redforge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
