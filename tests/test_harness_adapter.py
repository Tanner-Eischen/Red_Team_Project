from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from harborforge.adapters.call_budget import AgentBudgetExceededError, BudgetedLLM, CallBudget
from harborforge.adapters.harbor_adapter import HarborAdapter, classify_exception, parse_trial_result


def test_adapter_prefers_harbor_from_its_python_environment(monkeypatch, tmp_path):
    import os
    import harborforge.adapters.harbor_adapter as module

    python = tmp_path / ("python.exe" if os.name == "nt" else "python")
    harbor = tmp_path / ("harbor.exe" if os.name == "nt" else "harbor")
    harbor.touch()
    monkeypatch.delenv("HARBOR_CLI", raising=False)
    monkeypatch.setattr(module.sys, "executable", str(python))
    assert HarborAdapter().cli == str(harbor)
    assert HarborAdapter(cli="explicit-custom-harbor").cli == "explicit-custom-harbor"


def result_data(reward=1, **extra):
    return {"task_name": "etl", "trial_name": "etl__trial", "finished_at": "2026-10-31T00:00:00Z",
            "verifier_result": {"rewards": {"reward": reward}}, **extra}


@pytest.mark.parametrize(("reward", "outcome"), [(1, "PASS"), (0, "MODEL_FAIL"),
                                                   (None, "UNSCORABLE"), (True, "UNSCORABLE"),
                                                   (float("nan"), "UNSCORABLE"), (.5, "UNSCORABLE")])
def test_correctness_requires_binary_verifier_reward(reward, outcome):
    assert parse_trial_result(result_data(reward))["outcome"] == outcome


def test_incomplete_or_broken_verifier_never_scores_as_model_failure():
    assert parse_trial_result(result_data(1, finished_at=None))["outcome"] == "UNSCORABLE"
    for kind in ("VerifierTimeoutError", "DownloadVerifierDirError", "DockerError"):
        assert parse_trial_result(result_data(0, exception_info={"exception_type": kind}))["outcome"] == "INFRA_ERROR"
    assert parse_trial_result(result_data(0, exception_info={"exception_type": "RewardFileNotFoundError"}))["outcome"] == "UNSCORABLE"


@pytest.mark.parametrize(("kind", "outcome"), [("AuthenticationError", "PROVIDER_ERROR"),
                                                ("RateLimitError", "PROVIDER_ERROR"),
                                                ("AgentTimeoutError", "AGENT_TIMEOUT"),
                                                ("AgentBudgetExceededError", "AGENT_TIMEOUT"),
                                                ("RuntimeError", "INFRA_ERROR")])
def test_exception_stage_is_preserved(kind, outcome):
    assert classify_exception({"exception_type": kind})[0] == outcome


def test_adapter_uses_individual_trial_and_supported_oracle(monkeypatch, tmp_path):
    configs = []

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "0.13.2\n", "")
        config = json.loads(Path(command[-1]).read_text())
        configs.append(config)
        trial = Path(config["jobs_dir"]) / config["job_name"] / "etl__a"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps(result_data(0)))
        return subprocess.CompletedProcess(command, 0, "Job finished", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = HarborAdapter().run(tmp_path, {}, {"agent": "oracle"}, tmp_path / "run")
    assert result["outcome"] == "MODEL_FAIL"
    assert configs[0]["agents"][0]["name"] == "oracle"
    assert "model_name" not in configs[0]["agents"][0]
    assert configs[0]["retry"]["max_retries"] == 0


def test_successful_cli_without_trial_evidence_is_unscorable(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda command, **kw:
                        subprocess.CompletedProcess(command, 0, "0.13.2" if command[-1] == "--version" else "", ""))
    result = HarborAdapter().run(tmp_path, {}, {"agent": "nop"}, tmp_path / "run")
    assert result["outcome"] == "UNSCORABLE"
    assert result["reward"] is None


def test_adapter_refuses_drifted_harbor_and_output_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, "0.14.0", ""))
    destination = tmp_path / "run"
    result = HarborAdapter().run(tmp_path, {}, {"agent": "nop"}, destination)
    assert result["error_code"] == "HARBOR_VERSION_MISMATCH"
    with pytest.raises(FileExistsError):
        HarborAdapter().run(tmp_path, {}, {"agent": "nop"}, destination)


def test_model_agent_receives_pre_call_budget_guard(monkeypatch, tmp_path):
    configs = []

    def fake_run(command, **kw):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "0.13.2", "")
        configs.append(json.loads(Path(command[-1]).read_text()))
        return subprocess.CompletedProcess(command, 1, "", "missing daemon")

    monkeypatch.setattr(subprocess, "run", fake_run)
    model = {"model_id": "example/model", "max_input_tokens": 32000, "max_output_tokens": 8000,
             "max_cost_usd_per_attempt": 1, "input_usd_per_million": 1, "output_usd_per_million": 2}
    result = HarborAdapter().run(tmp_path, model, {"timeout_seconds": 47, "verifier_timeout_seconds": 19}, tmp_path / "run")
    assert result["outcome"] == "INFRA_ERROR"
    agent = configs[0]["agents"][0]
    assert agent["override_timeout_sec"] == 47
    assert agent["import_path"].endswith(":BudgetedTerminus2")
    assert agent["kwargs"]["campaign_budget"]["max_output_tokens"] == 8000
    assert configs[0]["verifier"]["override_timeout_sec"] == 19


def test_budget_reserves_failures_and_prevents_followup_calls():
    calls = []

    class LLM:
        async def call(self, prompt, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("provider failed after possible billing")

    budget = CallBudget(5000, 2000, 1, 1, 2)
    proxy = BudgetedLLM(LLM(), budget, 1000)
    with pytest.raises(RuntimeError):
        asyncio.run(proxy.call("first"))
    assert calls[0]["max_tokens"] == 1000
    assert calls[0]["num_retries"] == 0
    assert budget.usage_uncertain
    with pytest.raises(AgentBudgetExceededError):
        asyncio.run(proxy.call("second"))
    assert len(calls) == 1


def test_lost_provider_usage_never_refunds_uncertain_reservation():
    agent = {"n_input_tokens": 100, "n_output_tokens": 10, "cost_usd": .001,
             "metadata": {"budget_reservation": {"usage_uncertain": True, "input_reserved": 10000}}}
    result = parse_trial_result(result_data(0, agent_result=agent))
    assert result["tokens_input"] is None
    assert result["tokens_output"] is None
    assert result["cost_usd"] is None


def test_output_limit_cannot_reduce_the_reserved_counter():
    budget = CallBudget(10000, 2000, 1, 1, 2)
    with pytest.raises(AgentBudgetExceededError):
        budget.reserve("hello", {"max_tokens": -1})
    assert budget.output_reserved == 0


def test_budget_counts_history_schema_and_forbids_multimodal():
    budget = CallBudget(8000, 2000, 1, 1, 2)
    with pytest.raises(AgentBudgetExceededError):
        budget.reserve("hello", {"message_history": [{"content": "z" * 10000}]})
    with pytest.raises(AgentBudgetExceededError):
        budget.reserve("hello", {"message_history": [{"image_url": "data:image/png;base64,abc"}]})
    assert budget.input_reserved == 0


def test_tenacity_wrapper_is_bypassed_so_retries_need_new_reservations():
    calls = []

    class LLM:
        async def raw(self, prompt, **kwargs):
            calls.append("raw")
            return "ok"

        async def call(self, prompt, **kwargs):
            raise AssertionError("automatic retry decorator must not run")

        call.__wrapped__ = raw

    proxy = BudgetedLLM(LLM(), CallBudget(10000, 2000, 1, 1, 2), 1000)
    assert asyncio.run(proxy.call("test")) == "ok"
    assert calls == ["raw"]


def test_provider_overrun_stops_further_calls():
    calls = []

    class LLM:
        async def call(self, prompt, **kwargs):
            calls.append(prompt)
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=1001, cost_usd=.001))

    proxy = BudgetedLLM(LLM(), CallBudget(20000, 8000, 1, 1, 2), 1000)
    with pytest.raises(AgentBudgetExceededError):
        asyncio.run(proxy.call("first"))
    with pytest.raises(AgentBudgetExceededError):
        asyncio.run(proxy.call("second"))
    assert calls == ["first"]
