"""The launchers cannot load credentials or dispatch a paid configuration."""

import importlib.util
import io
import json
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


@pytest.fixture
def prepared_reference(monkeypatch, tmp_path):
    """Use real frozen task hashing but an offline stand-in for installed Harbor."""
    from harborforge.adapters import openrouter_free
    from harborforge.config import load_campaign

    module = script("prepare_free_pilot")
    harbor = tmp_path / "fake-harbor"
    for name in ("agents/terminus_2/templates/terminus-json-plain.txt",
                 "agents/terminus_2/terminus_json_plain_parser.py", "llms/lite_llm.py"):
        path = harbor / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("offline protocol fixture " + name, encoding="utf-8")
    original_spec = module.importlib.util.find_spec
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name, *args, **kwargs:
                        SimpleNamespace(origin=str(harbor / "__init__.py")) if name == "harbor" else
                        original_spec(name, *args, **kwargs))
    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "0.13.2")
    # Price/account transport verification has its own exhaustive tests; this
    # fixture isolates supplemental matrix construction without network or keys.
    monkeypatch.setattr(openrouter_free, "verify_free_evidence", lambda *args: None)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    rows = []
    for _, slug, _ in module.MODELS + module.FLINT:
        rows.append({"id": slug, "canonical_slug": slug.removesuffix(":free") + "-recorded"})
        (evidence / (slug.replace("/", "__").replace(":", "_") + "-endpoints.json")).write_text("{}")
    (evidence / "catalog.json").write_text(json.dumps({"data": rows}))
    (evidence / "account-safety.json").write_text("{}")
    (evidence / "retrieval.json").write_text(json.dumps({
        "verified_at_utc": module.datetime.now(module.timezone.utc).isoformat()}))
    reference_path = module.prepare(evidence, tmp_path / "initial")
    reference = load_campaign(reference_path)
    return module, evidence, reference_path, reference, harbor


def test_flint_supplement_reuses_exact_tasks_harness_and_bounded_protocol(prepared_reference, tmp_path):
    from harborforge.config import load_campaign
    from harborforge.evaluation.frozen import verify_suite

    module, evidence, reference_path, reference, _ = prepared_reference
    before = {str(path): path.read_bytes() for task in reference.tasks for path in Path(task).rglob("*") if path.is_file()}
    output = tmp_path / "flint"
    path = module.prepare(evidence, output, flint_supplement_from=reference_path)
    supplement = load_campaign(path)
    assert supplement.tasks == reference.tasks
    assert supplement.harness_profile == reference.harness_profile
    assert supplement.campaign_id != reference.campaign_id
    assert supplement.model_profiles[0].profile_id not in {model.profile_id for model in reference.model_profiles}
    assert len(supplement.model_profiles) == 1
    flint = supplement.model_profiles[0]
    assert flint.model_id == "openrouter/recursive/flint-v1.0:free"
    assert flint.agent_kwargs == reference.model_profiles[0].agent_kwargs
    assert flint.repeats == 2 and flint.billing_mode == "free_only"
    assert supplement.budgets.max_model_attempts == 6 and supplement.budgets.campaign_usd_max == 0
    assert supplement.budgets.total_input_tokens_max == 6 * 256000
    assert supplement.budgets.total_output_tokens_max == 6 * 98304
    assert supplement.budgets.max_provider_retries_total == 0
    assert not (output / "tasks").exists()
    assert all(Path(name).read_bytes() == content for name, content in before.items())
    old_suite = verify_suite(reference.suite_manifest, reference.model_dump(mode="json"))
    new_suite = verify_suite(supplement.suite_manifest, supplement.model_dump(mode="json"))
    assert old_suite["tasks"] == new_suite["tasks"]
    assert old_suite["suite_sha256"] != new_suite["suite_sha256"]
    link = json.loads((output / "reference-link.json").read_text())
    assert link["reference_campaign_sha256"] == module.digest(reference_path)
    assert link["reference_suite_sha256"] == old_suite["suite_sha256"]
    assert json.loads((output / "authorization.json").read_text())["maximum_inference_requests"] == 72
    # The historical default still builds the original three-model envelope.
    assert len(reference.model_profiles) == 3 and reference.budgets.max_model_attempts == 18


def test_flint_supplement_refuses_reference_task_drift_before_output(prepared_reference, tmp_path):
    module, evidence, reference_path, reference, _ = prepared_reference
    (Path(reference.tasks[0]) / "instruction.md").write_text("changed calibration task")
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="drift"):
        module.prepare(evidence, output, flint_supplement_from=reference_path)
    assert not output.exists()


def test_flint_supplement_refuses_changed_installed_harness(prepared_reference, tmp_path):
    module, evidence, reference_path, _, harbor = prepared_reference
    (harbor / "agents/terminus_2/terminus_json_plain_parser.py").write_text("changed parser")
    with pytest.raises(ValueError, match="harness/protocol"):
        module.prepare(evidence, tmp_path / "changed-harness", flint_supplement_from=reference_path)


def test_flint_catalog_refresh_only_fetches_selected_endpoint(monkeypatch, tmp_path):
    module = script("prepare_free_pilot")
    urls = []
    def retrieve(url, timeout):
        urls.append(url)
        return io.BytesIO(b'{"data": []}')
    monkeypatch.setattr(module, "urlopen", retrieve)
    module.refresh_public_evidence(tmp_path, module.FLINT)
    assert urls == ["https://openrouter.ai/api/v1/models",
                    "https://openrouter.ai/api/v1/models/recursive/flint-v1.0%3Afree/endpoints"]
    assert (tmp_path / "recursive__flint-v1.0_free-endpoints.json").exists()
