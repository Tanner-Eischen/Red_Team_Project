"""Compatibility entry point for the versioned HarborForge execution layer.

New campaigns should use ``redforge campaign run``. Legacy callers still get
explicit outcomes and independent verifier rewards instead of job-exit scores.
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from failure_logging import FailureLogger, StageResultRecord
from harborforge.adapters.harbor_adapter import HarborAdapter
from harborforge.validation import validate_task


@dataclass
class HarborRunResult:
    task_path: Path
    agent: str
    model: str
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    outcome: str = "UNSCORABLE"
    reward: float | None = None
    evidence_path: str | None = None


class HarborRunner:
    def __init__(self, tasks_root: str = "./adversarial_tasks") -> None:
        self.tasks_root = Path(tasks_root)

    def _execute(self, task_path: str, model: str, agent: str, timeout_seconds: int) -> HarborRunResult:
        destination = Path("artifacts") / "legacy-harbor" / uuid.uuid4().hex
        # Legacy model strings do not declare prices/token limits; do not incur
        # uncapped calls. A real model must go through a configured campaign.
        if agent not in {"oracle", "nop"}:
            return HarborRunResult(Path(task_path), agent, model, False, 2, "",
                                   "Use redforge campaign run with explicit model and budget profiles",
                                   outcome="UNSCORABLE")
        result = HarborAdapter().run(Path(task_path), {}, {"agent": agent, "timeout_seconds": timeout_seconds}, destination)
        return HarborRunResult(Path(task_path), agent, model, result["outcome"] == "PASS",
                               result.get("exit_code", 1), json.dumps(result), "", result["outcome"],
                               result["reward"], result["trace_path"])

    def oracle_run(self, task_path: str, timeout_seconds: int = 600) -> HarborRunResult:
        return self._execute(task_path, "", "oracle", timeout_seconds)

    def model_run(self, task_path: str, model: str, agent: str = "terminus-2", timeout_seconds: int = 600) -> HarborRunResult:
        return self._execute(task_path, model, agent, timeout_seconds)

    def oracle_gate(self, task_path: str, required_pass: bool = True, timeout_seconds: int = 600) -> HarborRunResult:
        first = self.oracle_run(task_path, timeout_seconds)
        second = self.oracle_run(task_path, timeout_seconds)
        if required_pass and (not first.success or not second.success):
            return first if not first.success else second
        return second

    def evaluate_models(self, task_path: str, models: list[str], agent: str = "terminus-2", timeout_seconds: int = 600) -> list[HarborRunResult]:
        return [self.model_run(task_path, model, agent, timeout_seconds) for model in models]

    def summarize_run(self, result: HarborRunResult) -> dict:
        return {**asdict(result), "task_path": str(result.task_path)}

    def dump_results_json(self, results: list[HarborRunResult], output_path: str) -> None:
        Path(output_path).write_text(json.dumps([self.summarize_run(result) for result in results], indent=2), encoding="utf-8")


def validate_pipeline(task_path: str, context_dir: str = "./.context", runtime: str = "docker") -> dict:
    logger = FailureLogger(context_dir)
    from datetime import datetime, timezone

    run_id = logger.create_run(task_id=Path(task_path).name, start_time=datetime.now(timezone.utc).isoformat())
    report = validate_task(Path(task_path), runtime=runtime, evidence_dir=logger.context_dir / "runs" / run_id)
    records = [StageResultRecord(run_id=run_id, stage_name=gate["name"],
                                status="pass" if gate["passed"] else "fail",
                                exit_code=0 if gate["passed"] else 1, runtime_ms=0,
                                logs_path=report["evidence_path"], error_signature_id=None)
               for gate in report["gates"]]
    logger.log_stage_results(records)
    logger.update_run_end_time(run_id, datetime.now(timezone.utc).isoformat())
    return {"run_id": run_id, "task_id": Path(task_path).name, **report}


def validate_task_verifier(task_path: str, logger: FailureLogger, run_id: str, timeout_seconds: int = 600) -> StageResultRecord:
    report = validate_task(Path(task_path), runtime="docker", evidence_dir=logger.context_dir / "runs" / run_id / "controls")
    return StageResultRecord(run_id=run_id, stage_name="verifier_controls", status="pass" if report["valid"] else "fail",
                             exit_code=0 if report["valid"] else 1, runtime_ms=int(report["wall_time_s"] * 1000),
                             logs_path=report["evidence_path"], error_signature_id=None)


def quick_evaluate_first_task(tasks_root: str = "./adversarial_tasks", models: list[str] | None = None,
                              timeout_seconds: int = 600) -> tuple[HarborRunResult, list[HarborRunResult]]:
    task = next(path for path in sorted(Path(tasks_root).iterdir()) if path.is_dir())
    runner = HarborRunner(tasks_root)
    oracle = runner.oracle_gate(str(task), timeout_seconds=timeout_seconds)
    return oracle, runner.evaluate_models(str(task), models or [], timeout_seconds=timeout_seconds) if oracle.success else []


def main() -> None:
    parser = argparse.ArgumentParser(description="Compatibility runner; prefer redforge")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--task", required=True)
    validate_parser.add_argument("--runtime", choices=("local", "docker"), default="docker")
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--context-dir", default="./.context")
    report_parser.add_argument("--output")
    args = parser.parse_args()
    if args.command == "validate":
        report = validate_pipeline(args.task, runtime=args.runtime)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["valid"] else 1)
    print(FailureLogger(args.context_dir).generate_html_report(args.output))


if __name__ == "__main__":
    main()
