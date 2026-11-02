"""The launchers cannot load credentials or dispatch a paid configuration."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def script(name):
    directory = Path(__file__).parents[1] / "scripts"
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location("pilot_script_" + name, directory / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(directory))


@pytest.mark.parametrize("cost,mode", [(1, "standard"), (0, "standard"), (1, "free_only")])
def test_pilot_launcher_refuses_nonfree_config_before_key_access(monkeypatch, tmp_path, cost, mode):
    module = script("run_free_pilot")
    config = SimpleNamespace(objective="fixed_evaluation", cohort="calibration",
        budgets=SimpleNamespace(campaign_usd_max=cost), model_profiles=[SimpleNamespace(billing_mode=mode)])
    monkeypatch.setattr(module, "load_campaign", lambda path: config)
    def forbidden(path):
        raise AssertionError("A refused configuration reached credential loading")
    monkeypatch.setattr(module, "load_existing_key", forbidden)
    monkeypatch.setattr(sys, "argv", ["run_free_pilot", "--campaign", str(tmp_path / "config.yaml"),
        "--key-file", str(tmp_path / "private.env"), "--output-dir", str(tmp_path / "run")])
    with pytest.raises(ValueError, match="zero-price"):
        module.main()
    assert not (tmp_path / "run").exists()


def test_credential_loader_refuses_other_provider_keys(monkeypatch, tmp_path):
    module = script("probe_openrouter_free")
    path = tmp_path / "private.env"
    path.write_text("OPENAI_API_KEY=not-an-openrouter-credential\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="no OpenRouter credential"):
        module.load_existing_key(path)
    assert "OPENROUTER_API_KEY" not in module.os.environ


def test_credential_loader_only_uses_requested_setting(monkeypatch, tmp_path, capsys):
    module = script("probe_openrouter_free")
    path = tmp_path / "private.env"
    recording = "sk-or-" + "recording-only-never-a-real-credential"
    path.write_text("UNRELATED_SETTING=do-not-import\nOPENROUTER_API_KEY='" + recording + "'\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "previous")
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "previous")
    monkeypatch.setenv("LITELLM_LOG", "previous")
    monkeypatch.delenv("UNRELATED_SETTING", raising=False)
    module.load_existing_key(path)
    assert module.os.environ["OPENROUTER_API_KEY"] == recording
    assert "UNRELATED_SETTING" not in module.os.environ
    assert capsys.readouterr().out == ""
