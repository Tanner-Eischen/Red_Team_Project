from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from failure_logging import FailureLogger, StageResultRecord


@dataclass
class HarborRunResult:
    task_path: Path
    agent: str
    model: str
    success: bool
    exit_code: int
    stdout: str
    stderr: str


class HarborRunner:
    def __init__(self, tasks_root: str = "./adversarial_tasks") -> None:
        self.tasks_root = Path(tasks_root)

    def _run(self, args: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _harbor_base_cmd(self) -> List[str]:
        base = os.environ.get("HARBOR_CLI")
        if base:
            return [base]
        return ["harbor"]

    def oracle_run(self, task_path: str, timeout_seconds: int = 600) -> HarborRunResult:
        task_dir = Path(task_path)
        cmd = self._harbor_base_cmd() + [
            "run",
            "-a",
            "terminus-2",
            "-m",
            "oracle/local",
            "-p",
            str(task_dir),
        ]
        proc = self._run(cmd, cwd=task_dir, timeout=timeout_seconds)
        success = proc.returncode == 0
        return HarborRunResult(
            task_path=task_dir,
            agent="terminus-2",
            model="oracle/local",
            success=success,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def model_run(self, task_path: str, model: str, agent: str = "terminus-2", timeout_seconds: int = 600) -> HarborRunResult:
        task_dir = Path(task_path)
        cmd = self._harbor_base_cmd() + [
            "run",
            "-a",
            agent,
            "-m",
            model,
            "-p",
            str(task_dir),
        ]
        proc = self._run(cmd, cwd=task_dir, timeout=timeout_seconds)
        success = proc.returncode == 0
        return HarborRunResult(
            task_path=task_dir,
            agent=agent,
            model=model,
            success=success,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def oracle_gate(self, task_path: str, required_pass: bool = True, timeout_seconds: int = 600) -> HarborRunResult:
        first = self.oracle_run(task_path, timeout_seconds=timeout_seconds)
        second = self.oracle_run(task_path, timeout_seconds=timeout_seconds)
        if required_pass and (not first.success or not second.success):
            return HarborRunResult(
                task_path=Path(task_path),
                agent="terminus-2",
                model="oracle/local",
                success=False,
                exit_code=1,
                stdout=first.stdout + second.stdout,
                stderr=first.stderr + second.stderr,
            )
        return second

    def evaluate_models(
        self,
        task_path: str,
        models: List[str],
        agent: str = "terminus-2",
        timeout_seconds: int = 600,
    ) -> List[HarborRunResult]:
        results: List[HarborRunResult] = []
        for model in models:
            res = self.model_run(task_path, model=model, agent=agent, timeout_seconds=timeout_seconds)
            results.append(res)
        return results

    def summarize_run(self, result: HarborRunResult) -> Dict[str, Any]:
        return {
            "task_path": str(result.task_path),
            "agent": result.agent,
            "model": result.model,
            "success": result.success,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def dump_results_json(self, results: List[HarborRunResult], output_path: str) -> None:
        data = [self.summarize_run(r) for r in results]
        Path(output_path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _stage_log_dir(logger: FailureLogger, run_id: str) -> Path:
    base = logger.context_dir / "runs" / run_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_stage_log(log_dir: Path, stage_name: str, stdout: str, stderr: str) -> str:
    path = log_dir / f"{stage_name}.log"
    content = ""
    if stdout:
        content += stdout
        if not content.endswith("\n"):
            content += "\n"
    if stderr:
        content += stderr
        if not content.endswith("\n"):
            content += "\n"
    path.write_text(content, encoding="utf-8")
    return str(path)


def validate_task_static(task_path: str, logger: FailureLogger, run_id: str) -> StageResultRecord:
    task_dir = Path(task_path)
    stage = "static"
    start = time.perf_counter()
    required = [
        task_dir / "instruction.md",
        task_dir / "task.toml",
        task_dir / "environment" / "Dockerfile",
        task_dir / "solution" / "solve.sh",
        task_dir / "tests" / "test.sh",
        task_dir / "tests" / "test_outputs.py",
    ]
    missing: List[str] = []
    for p in required:
        if not p.exists():
            missing.append(str(p.relative_to(task_dir)))
    log_dir = _stage_log_dir(logger, run_id)
    if missing:
        text = "Missing required files:\n" + "\n".join(missing) + "\n"
        log_path = _write_stage_log(log_dir, stage, "", text)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return StageResultRecord(
            run_id=run_id,
            stage_name=stage,
            status="fail",
            exit_code=1,
            runtime_ms=duration_ms,
            logs_path=log_path,
            error_signature_id=None,
        )
    text = "All required files present\n"
    log_path = _write_stage_log(log_dir, stage, text, "")
    duration_ms = int((time.perf_counter() - start) * 1000)
    return StageResultRecord(
        run_id=run_id,
        stage_name=stage,
        status="pass",
        exit_code=0,
        runtime_ms=duration_ms,
        logs_path=log_path,
        error_signature_id=None,
    )


def validate_task_build(task_path: str, logger: FailureLogger, run_id: str, tag_suffix: str = "") -> StageResultRecord:
    task_dir = Path(task_path)
    stage = "build"
    start = time.perf_counter()
    log_dir = _stage_log_dir(logger, run_id)
    tag = f"harborforge-{task_dir.name}"
    if tag_suffix:
        tag += f"-{tag_suffix}"
    cmd = [
        "docker",
        "build",
        "-f",
        str(task_dir / "environment" / "Dockerfile"),
        "-t",
        tag,
        str(task_dir),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(task_dir),
            capture_output=True,
            text=True,
        )
        log_path = _write_stage_log(log_dir, stage, proc.stdout, proc.stderr)
        duration_ms = int((time.perf_counter() - start) * 1000)
        status = "pass" if proc.returncode == 0 else "fail"
        return StageResultRecord(
            run_id=run_id,
            stage_name=stage,
            status=status,
            exit_code=proc.returncode,
            runtime_ms=duration_ms,
            logs_path=log_path,
            error_signature_id=None,
        )
    except FileNotFoundError as e:
        log_path = _write_stage_log(log_dir, stage, "", str(e))
        duration_ms = int((time.perf_counter() - start) * 1000)
        return StageResultRecord(
            run_id=run_id,
            stage_name=stage,
            status="fail",
            exit_code=1,
            runtime_ms=duration_ms,
            logs_path=log_path,
            error_signature_id=None,
        )


def validate_task_oracle(task_path: str, logger: FailureLogger, run_id: str, timeout_seconds: int = 600) -> StageResultRecord:
    stage = "oracle"
    start = time.perf_counter()
    runner = HarborRunner()
    result = runner.oracle_gate(task_path, timeout_seconds=timeout_seconds)
    log_dir = _stage_log_dir(logger, run_id)
    log_path = _write_stage_log(log_dir, stage, result.stdout, result.stderr)
    duration_ms = int((time.perf_counter() - start) * 1000)
    status = "pass" if result.success else "fail"
    return StageResultRecord(
        run_id=run_id,
        stage_name=stage,
        status=status,
        exit_code=result.exit_code,
        runtime_ms=duration_ms,
        logs_path=log_path,
        error_signature_id=None,
    )


def validate_task_verifier(task_path: str, logger: FailureLogger, run_id: str, timeout_seconds: int = 600) -> StageResultRecord:
    task_dir = Path(task_path)
    stage = "verifier"
    start = time.perf_counter()
    log_dir = _stage_log_dir(logger, run_id)
    cmd = ["bash", str(task_dir / "tests" / "test.sh")]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(task_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as e:
        log_path = _write_stage_log(log_dir, stage, "", str(e))
        duration_ms = int((time.perf_counter() - start) * 1000)
        return StageResultRecord(
            run_id=run_id,
            stage_name=stage,
            status="fail",
            exit_code=1,
            runtime_ms=duration_ms,
            logs_path=log_path,
            error_signature_id=None,
        )
    status = "pass" if proc.returncode == 0 else "fail"
    log_path = _write_stage_log(log_dir, stage, proc.stdout, proc.stderr)
    duration_ms = int((time.perf_counter() - start) * 1000)
    return StageResultRecord(
        run_id=run_id,
        stage_name=stage,
        status=status,
        exit_code=proc.returncode,
        runtime_ms=duration_ms,
        logs_path=log_path,
        error_signature_id=None,
    )


def validate_pipeline(task_path: str, context_dir: str = "./.context") -> Dict[str, Any]:
    logger = FailureLogger(context_dir)
    task_dir = Path(task_path)
    task_id = task_dir.name
    start_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    run_id = logger.create_run(task_id=task_id, start_time=start_iso)
    stage_records: List[StageResultRecord] = []
    static_result = validate_task_static(str(task_dir), logger, run_id)
    stage_records.append(static_result)
    if static_result.status == "pass":
        build_result = validate_task_build(str(task_dir), logger, run_id)
        stage_records.append(build_result)
        if build_result.status == "pass":
            oracle_result = validate_task_oracle(str(task_dir), logger, run_id)
            stage_records.append(oracle_result)
            if oracle_result.status == "pass":
                verifier_result = validate_task_verifier(str(task_dir), logger, run_id)
                stage_records.append(verifier_result)
    logger.log_stage_results(stage_records)
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    logger.update_run_end_time(run_id, end_iso)
    summary: Dict[str, Any] = {
        "run_id": run_id,
        "task_id": task_id,
        "stages": [
            {
                "stage_name": rec.stage_name,
                "status": rec.status,
                "exit_code": rec.exit_code,
                "runtime_ms": rec.runtime_ms,
                "logs_path": rec.logs_path,
            }
            for rec in stage_records
        ],
    }
    return summary


def quick_evaluate_first_task(
    tasks_root: str = "./adversarial_tasks",
    models: Optional[List[str]] = None,
    timeout_seconds: int = 600,
) -> Tuple[HarborRunResult, List[HarborRunResult]]:
    root = Path(tasks_root)
    task_dir = next(p for p in root.iterdir() if p.is_dir())
    runner = HarborRunner(tasks_root)
    oracle_result = runner.oracle_gate(str(task_dir), timeout_seconds=timeout_seconds)
    use_models = models or []
    model_results = runner.evaluate_models(str(task_dir), models=use_models, timeout_seconds=timeout_seconds)
    return oracle_result, model_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Harbor runner and validation utilities")
    sub = parser.add_subparsers(dest="command")
    validate_parser = sub.add_parser("validate", help="Validate a Harbor 2.0 task directory")
    validate_parser.add_argument("--task", required=True, help="Path to task directory")
    report_parser = sub.add_parser("report", help="Generate HTML analytics report from HarborForge registry")
    report_parser.add_argument("--context-dir", default="./.context", help="Path to context directory")
    report_parser.add_argument("--output", help="Output HTML file path")
    args = parser.parse_args()
    if args.command == "validate":
        summary = validate_pipeline(args.task)
        print(json.dumps(summary, indent=2))
    elif args.command == "report":
        logger = FailureLogger(args.context_dir)
        path = logger.generate_html_report(args.output)
        print(path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
