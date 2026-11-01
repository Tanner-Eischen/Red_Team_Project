"""A narrow, pinned Harbor CLI adapter with evidence-based result parsing.

Harbor's process exit code describes a job, not task correctness. Only one
completed trial with a well-formed verifier reward can establish correctness.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HARBOR_VERSION = "0.13.2"


def redact(text: str) -> str:
    """Remove known credential values before recording orchestration output."""
    for name, value in os.environ.items():
        if any(term in name.upper() for term in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) > 7:
            text = text.replace(value, "[REDACTED]")
    return re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+", r"\1[REDACTED]", text)


def classify_exception(exception: dict[str, Any]) -> tuple[str, str]:
    """Classify stage-specific errors without blaming a model for its tools."""
    kind = str(exception.get("exception_type", "UnknownError"))
    message = str(exception.get("exception_message", ""))
    if kind == "AgentBudgetExceededError":
        return "AGENT_TIMEOUT", "budget_exhausted"
    if kind == "AgentTimeoutError":
        return "AGENT_TIMEOUT", kind
    if any(term in kind for term in ("Authentication", "RateLimit", "PermissionDenied", "APIConnection", "APIError")):
        return "PROVIDER_ERROR", kind
    if kind in {"BadRequestError", "NotFoundError", "ServiceUnavailableError", "ContextWindowExceededError"}:
        return "PROVIDER_ERROR", kind
    if kind in {"RewardFileNotFoundError", "RewardFileEmptyError", "VerifierOutputParseError"}:
        return "UNSCORABLE", kind
    # Provider libraries sometimes wrap their errors in a generic exception.
    if any(term in message.lower() for term in ("invalid api key", "authentication failed", "rate limit exceeded")):
        return "PROVIDER_ERROR", kind
    return "INFRA_ERROR", kind


def parse_trial_result(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize an individual Harbor 0.13.2 trial, never a job aggregate."""
    result: dict[str, Any] = {
        "outcome": "UNSCORABLE", "reward": None, "verifier_status": "missing",
        "tokens_input": None, "tokens_output": None, "cost_usd": None, "error_code": None,
        "agent_info": data.get("agent_info"), "harbor_trial_id": data.get("id"),
    }
    agent = data.get("agent_result") or {}
    result.update(tokens_input=agent.get("n_input_tokens"), tokens_output=agent.get("n_output_tokens"),
                  cost_usd=agent.get("cost_usd"))
    reservation = (agent.get("metadata") or {}).get("budget_reservation")
    if reservation is not None:
        result["budget_reservation"] = reservation
        if reservation.get("usage_uncertain"):
            result.update(tokens_input=None, tokens_output=None, cost_usd=None)
    if data.get("exception_info"):
        outcome, error = classify_exception(data["exception_info"])
        result.update(outcome=outcome, error_code=error, verifier_status="error")
        return result
    # A stale reward does not turn an interrupted trial into a completed one.
    if not data.get("finished_at"):
        result["error_code"] = "INCOMPLETE_TRIAL"
        return result
    verifier = data.get("verifier_result") or {}
    rewards = verifier.get("rewards")
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if isinstance(reward, bool) or not isinstance(reward, (float, int)) or not math.isfinite(reward):
        result["error_code"] = "MISSING_OR_INVALID_REWARD"
        return result
    if reward not in (0, 1):
        result["error_code"] = "NON_BINARY_REWARD"
        return result
    result.update(outcome="PASS" if reward == 1 else "MODEL_FAIL", reward=float(reward), verifier_status="complete")
    return result


class HarborAdapter:
    """Run precisely one task/model/repeat in an immutable output directory."""

    def __init__(self, cli: str | None = None) -> None:
        environment_cli = Path(sys.executable).with_name("harbor.exe" if os.name == "nt" else "harbor")
        self.cli = cli or os.environ.get("HARBOR_CLI") or (str(environment_cli) if environment_cli.is_file() else "harbor")

    def run(self, task_path: Path, model: dict, harness: dict, output_dir: Path) -> dict:
        started = time.monotonic()
        task_path, output_dir = Path(task_path).resolve(), Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=False)
        base = {
            "outcome": "INFRA_ERROR", "reward": None, "verifier_status": "not_run",
            "tokens_input": None, "tokens_output": None, "cost_usd": None,
            "error_code": None, "trace_path": str(output_dir), "harbor_version": HARBOR_VERSION,
        }
        try:
            version = subprocess.run([self.cli, "--version"], capture_output=True, text=True, timeout=15)
            actual_version = version.stdout.strip()
            if version.returncode or actual_version != HARBOR_VERSION:
                base.update(error_code="HARBOR_VERSION_MISMATCH", actual_harbor_version=actual_version)
                return self._finish(base, started, output_dir)
            agent_name = harness.get("agent_id", harness.get("agent", "terminus-2"))
            kwargs = dict(harness.get("agent_kwargs") or {})
            kwargs.update(model.get("agent_kwargs") or {})
            if agent_name not in {"oracle", "nop"}:
                for key in ("temperature", "reasoning_effort"):
                    if model.get(key) is not None:
                        kwargs[key] = model[key]
                if model.get("max_output_tokens") is not None:
                    kwargs.setdefault("llm_call_kwargs", {})["max_tokens"] = model["max_output_tokens"]
            agent_config = {
                "name": agent_name,
                "override_timeout_sec": harness.get("agent_time_limit_sec", harness.get("timeout_seconds", 600)),
                "override_setup_timeout_sec": harness.get("agent_setup_time_limit_sec", 120),
                "kwargs": kwargs,
            }
            if agent_name not in {"oracle", "nop"}:
                agent_config["model_name"] = model["model_id"]
                if agent_name != "terminus-2":
                    raise ValueError("Only the budgeted Terminus2 terminal agent is supported")
                kwargs["campaign_budget"] = {
                    "max_input_tokens": model["max_input_tokens"],
                    "max_output_tokens": model["max_output_tokens"],
                    "max_cost_usd": model["max_cost_usd_per_attempt"],
                    "input_usd_per_million": model["input_usd_per_million"],
                    "output_usd_per_million": model["output_usd_per_million"],
                }
                kwargs["per_call_output_tokens"] = min(model["max_output_tokens"], 2048)
                agent_config["name"] = None
                agent_config["import_path"] = "harborforge.adapters.budgeted_agent:BudgetedTerminus2"
            config = {
                "job_name": "trial", "jobs_dir": str(output_dir / "jobs"),
                "n_attempts": 1, "n_concurrent_trials": 1, "quiet": True,
                "retry": {"max_retries": 0},
                "environment": {"type": "docker", "delete": True,
                                "cpu_enforcement_policy": "limit", "memory_enforcement_policy": "limit",
                                "override_cpus": harness.get("cpus", 1),
                                "override_memory_mb": harness.get("memory_mb", 2048)},
                "verifier": {"override_timeout_sec": harness.get("verifier_time_limit_sec", harness.get("verifier_timeout_seconds", 120))},
                "agents": [agent_config], "tasks": [{"path": str(task_path)}],
            }
            config_path = output_dir / "job-config.json"
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            timeout = float(harness.get("trial_time_limit_sec", harness.get("timeout_seconds", 600) + 900))
            child_env = os.environ.copy()
            package_root = str(Path(__file__).resolve().parents[2])
            child_env["PYTHONPATH"] = package_root + os.pathsep + child_env.get("PYTHONPATH", "")
            proc = subprocess.run([self.cli, "run", "--config", str(config_path)],
                                  capture_output=True, text=True, timeout=timeout, env=child_env)
            (output_dir / "stdout.log").write_text(redact(proc.stdout), encoding="utf-8")
            (output_dir / "stderr.log").write_text(redact(proc.stderr), encoding="utf-8")
            base["exit_code"] = proc.returncode
            trials = []
            for result_path in (output_dir / "jobs" / "trial").glob("*/result.json"):
                try:
                    value = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if "trial_name" in value and "task_name" in value:
                    trials.append((result_path, value))
            if len(trials) != 1:
                base.update(outcome="UNSCORABLE" if proc.returncode == 0 else "INFRA_ERROR",
                            error_code="EXPECTED_ONE_TRIAL_RESULT")
            else:
                result_path, value = trials[0]
                base.update(parse_trial_result(value), result_path=str(result_path))
                # A failed job without a per-trial exception is not a clean scored run.
                if proc.returncode != 0 and base["outcome"] in {"PASS", "MODEL_FAIL"}:
                    base.update(outcome="INFRA_ERROR", reward=None, error_code="HARBOR_JOB_FAILED")
        except subprocess.TimeoutExpired:
            base.update(error_code="HARNESS_WALL_TIMEOUT")
        except (OSError, ValueError, KeyError) as exc:
            base.update(error_code=type(exc).__name__, error_message=redact(str(exc)))
        return self._finish(base, started, output_dir)

    @staticmethod
    def _finish(result: dict, started: float, output_dir: Path) -> dict:
        result["wall_time_s"] = time.monotonic() - started
        (output_dir / "normalized-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
