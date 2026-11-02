"""Trusted pinned wheel assets may be linked; candidate artifacts may not."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("harbor")
from harborforge.adapters import isolated_docker as isolated


def installed_tool():
    distribution = metadata.distribution("harbor")
    entry = next(entry for entry in distribution.files if entry.as_posix() == isolated.TRUSTED_HARBOR_ASSET)
    return distribution, entry, Path(distribution.locate_file(entry))


def test_trusted_helper_must_match_actual_pinned_wheel_record(tmp_path):
    _, _, source = installed_tool()
    assert isolated._verified_harbor_tool(source, isolated.TRUSTED_HARBOR_TARGET) == source.read_bytes()
    copied = tmp_path / source.name
    copied.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="outside the pinned"):
        isolated._verified_harbor_tool(copied, isolated.TRUSTED_HARBOR_TARGET)
    with pytest.raises(ValueError, match="approved trusted tool"):
        isolated._verified_harbor_tool(source, "/app/pipeline.py")


def test_trusted_helper_record_mismatch_rejects_without_modifying_installed_asset(monkeypatch):
    _, entry, source = installed_tool()
    wrong_entry = SimpleNamespace(as_posix=lambda: isolated.TRUSTED_HARBOR_ASSET,
        size=entry.size, hash=SimpleNamespace(mode="sha256", value="invalid-record"))
    fake = SimpleNamespace(version="0.13.2", files=[wrong_entry], locate_file=lambda item: source)
    monkeypatch.setattr(isolated.metadata, "distribution", lambda name: fake)
    with pytest.raises(ValueError, match="differs"):
        isolated._verified_harbor_tool(source, isolated.TRUSTED_HARBOR_TARGET)


def test_uv_hardlinked_tool_is_staged_as_fresh_regular_bytes(monkeypatch, tmp_path):
    _, _, source = installed_tool()
    if source.stat().st_nlink <= 1:
        pytest.skip("This installation does not use uv hardlinked package assets")
    uploads = []
    class Process:
        returncode = 0
        async def communicate(self, input=None):
            uploads.append(json.loads(input))
            return b"", b""
    async def process(*args, **kwargs):
        return Process()
    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", process)
    environment = isolated.IsolatedDockerEnvironment.__new__(isolated.IsolatedDockerEnvironment)
    environment.trial_paths = SimpleNamespace(trial_dir=tmp_path)
    environment._container_id = "recording-container"
    asyncio.run(environment.upload_file(source, isolated.TRUSTED_HARBOR_TARGET))
    assert base64.b64decode(uploads[0][0]["data"]) == source.read_bytes()
    proof = json.loads((tmp_path / "trusted-tooling.jsonl").read_text())
    assert proof["original_links"] > 1 and proof["staged_links"] == 1
    assert proof["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    arbitrary = tmp_path / "candidate.py"
    arbitrary.write_text("pass")
    os.link(arbitrary, tmp_path / "candidate-alias.py")
    with pytest.raises(ValueError):
        asyncio.run(environment.upload_file(arbitrary, "/app/pipeline.py"))


@pytest.mark.skipif(os.name != "posix" or os.environ.get("HF_RUN_DOCKER_SETUP") != "1",
                   reason="Explicit native Docker-only no-model setup probe")
def test_real_budgeted_terminus_setup_and_recording_without_any_model_call(tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from test_openrouter_free import MODEL, policy
    from harbor.models.task.config import NetworkPolicy, TaskConfig
    from harbor.models.trial.paths import TrialPaths
    from harborforge.adapters.budgeted_agent import BudgetedTerminus2
    from harborforge.task_families.registry import generate_task
    import litellm
    import tomllib
    async def forbid_model(**kwargs):
        raise AssertionError("Setup probe must never call a model")
    monkeypatch.setattr(litellm, "acompletion", forbid_model)
    evidence = tmp_path / "public-recording-policy"
    evidence.mkdir()
    free = policy.__wrapped__(evidence)
    task = generate_task("scientific-etl", 20261103, tmp_path / "tasks")
    config = TaskConfig.model_validate(tomllib.loads((task / "task.toml").read_text()))
    paths = TrialPaths(tmp_path / "trial")
    paths.mkdir()
    environment = isolated.IsolatedDockerEnvironment(environment_dir=task / "environment",
        environment_name="hf-terminus-setup-no-model", session_id="hf-setup-" + tmp_path.name[-12:].lower(),
        trial_paths=paths, task_env_config=config.environment,
        network_policy=NetworkPolicy(network_mode="no-network"),
        cpu_enforcement_policy="limit", memory_enforcement_policy="limit")
    budget = {"max_input_tokens": 10000, "max_output_tokens": 1000, "max_cost_usd": 0,
              "input_usd_per_million": 0, "output_usd_per_million": 0}
    agent = BudgetedTerminus2(logs_dir=paths.agent_dir, model_name=MODEL, campaign_budget=budget,
                             free_routing=free, record_terminal_session=True)
    async def run():
        try:
            await environment.start(force_build=False)
            assert (await environment.exec("asciinema --version", user="root")).return_code == 0
            with environment.with_default_user("agent"):
                await agent.setup(environment)
                await agent._session.send_keys(keys=["printf 'HF_SETUP_RECORDING_OK\\n'", "Enter"], min_timeout_sec=.2)
                assert (await environment.exec("test -s /logs/agent/recording.cast", user="agent")).return_code == 0
                await agent._session.stop()
            assert agent._free_llm.requests == 0
            assert paths.agent_dir.joinpath("recording.cast").stat().st_size > 0
            assert b"HF_SETUP_RECORDING_OK" in paths.agent_dir.joinpath("recording.cast").read_bytes()
            tooling = [json.loads(line) for line in (paths.trial_dir / "trusted-tooling.jsonl").read_text().splitlines()]
            assert tooling[0]["original_links"] > 1 and tooling[0]["staged_links"] == 1
        finally:
            await environment.stop(delete=True)
    asyncio.run(run())
