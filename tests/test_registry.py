import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from harborforge.authoring.provenance import task_digest
from harborforge.storage import ArtifactStore, Registry


def make_task(tmp_path, *, task_id="example", cohort="discovery", protocol="terminal_agent", version=1,
              parent=None):
    path = tmp_path / f"{task_id}-v{version}"
    path.mkdir()
    (path / "instruction.md").write_text("Implement the contract.\n", encoding="utf-8")
    manifest = {"task_id": task_id, "task_version_id": f"{task_id}-v{version}", "version": version,
                "family": "scientific-etl", "protocol": protocol, "cohort": cohort,
                "parent_task_version_id": parent, "artifact_sha256": ""}
    manifest_path = path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest["artifact_sha256"] = task_digest(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def prepare(registry, tmp_path, **task_kwargs):
    path, manifest = make_task(tmp_path, **task_kwargs)
    task_id = registry.register_task(manifest, path)
    validation_id = registry.record_validation(task_id, {"valid": True, "runtime": "docker",
                                                        "eligible_for_model_evaluation": True,
                                                        "task_sha256": manifest["artifact_sha256"]})
    model_id = registry.register_profile("model", {"profile_id": "test-model", "model_id": "offline-test",
                                                    "repeats": 2})
    harness_id = registry.register_profile("harness", {"profile_id": "test-harness", "protocol": "terminal_agent"})
    campaign_id = registry.create_campaign({"campaign_id": "test-campaign", "cohort": manifest["cohort"],
                                            "protocol": manifest["protocol"], "tasks": [str(path.resolve())],
                                            "objective": "fixed_evaluation" if manifest["cohort"] == "holdout"
                                            else "adversarial_discovery",
                                            "model_profiles": [{"profile_id": "test-model", "repeats": 2}]})
    return {"campaign_id": campaign_id, "task_version_id": task_id, "validation_id": validation_id,
            "model_profile_id": model_id, "harness_profile_id": harness_id}


def test_registry_records_success_failure_and_errors_and_refuses_mutation(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        for index, (outcome, reward) in enumerate([("PASS", 1), ("MODEL_FAIL", 0), ("PROVIDER_ERROR", None)]):
            registry.record_trial({**base, "repeat_index": index, "outcome": outcome, "reward": reward})
        assert {row["outcome"] for row in registry.list_trials("test-campaign")} == {
            "PASS", "MODEL_FAIL", "PROVIDER_ERROR"}
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            registry.connection.execute("DELETE FROM trials")
        with pytest.raises(sqlite3.IntegrityError):
            registry.record_trial({**base, "repeat_index": 0, "outcome": "MODEL_FAIL", "reward": 0})
        assert registry.connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_campaign_and_profile_identifiers_are_immutable(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        assert registry.create_campaign({"campaign_id": "a"}) == "a"
        with pytest.raises(ValueError, match="preregistration"):
            registry.create_campaign({"campaign_id": "a", "extra": "changed"})
        registry.register_profile("model", {"profile_id": "m", "model_id": "a"})
        with pytest.raises(ValueError, match="immutable"):
            registry.register_profile("model", {"profile_id": "m", "model_id": "b"})
        with pytest.raises(ValueError, match="credentials"):
            registry.register_profile("model", {"api_key": "anything"})


def test_task_and_validation_drift_are_rejected(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        path, manifest = make_task(tmp_path)
        task_id = registry.register_task(manifest, path)
        assert registry.register_task(manifest, path) == task_id
        with pytest.raises(ValueError, match="exact registered"):
            registry.record_validation(task_id, {"valid": True, "task_sha256": "wrong"})
        (path / "instruction.md").write_text("Changed requirements", encoding="utf-8")
        with pytest.raises(ValueError, match="manifest hash"):
            registry.register_task(manifest, path)


def test_correctness_requires_valid_matching_quality_evidence(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        no_validation = {**base, "validation_id": None, "repeat_index": 0, "outcome": "PASS", "reward": 1}
        with pytest.raises(ValueError, match="quality gate"):
            registry.record_trial(no_validation)
        with pytest.raises(ValueError, match="full verifier"):
            registry.record_trial({**no_validation, "validation_id": base["validation_id"], "reward": 0})
        with pytest.raises(ValueError, match="finite"):
            registry.record_trial({**base, "outcome": "MODEL_FAIL", "reward": float("nan")})
        error_id = registry.record_trial({**no_validation, "outcome": "INFRA_ERROR", "reward": None})
        assert error_id.startswith("trial-")


def test_protocol_and_holdout_boundaries(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        with pytest.raises(ValueError, match="Holdout"):
            registry.create_campaign({"campaign_id": "bad", "cohort": "holdout"})
        base = prepare(registry, tmp_path)
        other_harness = registry.register_profile("harness", {"profile_id": "qa", "protocol": "qa_completion"})
        with pytest.raises(ValueError, match="protocols"):
            registry.record_trial({**base, "harness_profile_id": other_harness, "outcome": "PASS", "reward": 1})


def test_local_or_ineligible_controls_cannot_support_model_scores(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        task = registry.get_task(base["task_version_id"])
        for runtime, eligible in (("local", True), ("docker", False), ("docker", None)):
            validation_id = registry.record_validation(base["task_version_id"], {
                "valid": True, "runtime": runtime, "eligible_for_model_evaluation": eligible,
                "task_sha256": task["artifact_sha256"],
            })
            with pytest.raises(ValueError, match="eligible Docker"):
                registry.record_trial({**base, "validation_id": validation_id, "outcome": "PASS", "reward": 1})


def test_event_sequence_redaction_and_append_only_reviews(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        trial_id = registry.record_trial({**base, "outcome": "MODEL_FAIL", "reward": 0})
        assert registry.append_event(trial_id, "tool_result", {"api_key": "private", "tokens_input": 12}) == 0
        assert registry.append_event(trial_id, "verifier_result", {"text": "AssertionError"}) == 1
        event = registry.list_events(trial_id)[0]
        assert event["payload"] == {"api_key": "[REDACTED]", "tokens_input": 12}
        finding = registry.record_finding(trial_id, "behavioral_assertion", 1, evidence="AssertionError")
        registry.review_finding(finding, "reviewer-one", "confirmed", "Observed artifact error")
        registry.review_finding(finding, "reviewer-two", "uncertain", "Need independent reproduction")
        assert len(registry.list_findings("test-campaign")[0]["reviews"]) == 2
        exported = registry.export_events(trial_id, tmp_path / "events.jsonl")
        assert len(exported.read_text().splitlines()) == 2


def test_events_from_independent_connections_get_unique_sequences(tmp_path):
    database = tmp_path / "registry.sqlite"
    with Registry(database) as registry:
        trial_id = registry.record_trial({**prepare(registry, tmp_path), "outcome": "PROVIDER_ERROR"})

    def append(index):
        with Registry(database) as registry:
            return registry.append_event(trial_id, "concurrent", {"index": index})

    with ThreadPoolExecutor(max_workers=4) as pool:
        sequences = list(pool.map(append, range(12)))
    assert sorted(sequences) == list(range(12))


def test_content_store_detects_tampering_and_path_traversal(tmp_path):
    store = ArtifactStore(tmp_path / "objects")
    item = store.put(b"scientific trace")
    assert store.get(item["sha256"]) == b"scientific trace"
    assert store.put(b"scientific trace") == item
    with pytest.raises(ValueError):
        store.path_for("../../outside")
    store.path_for(item["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.get(item["sha256"])
    with pytest.raises(ValueError):
        store.put(b"scientific trace")


def test_execution_claim_survives_restart_without_completed_trials(tmp_path):
    database = tmp_path / "registry.sqlite"
    with Registry(database) as registry:
        registry.create_campaign({"campaign_id": "interrupted"})
        assert registry.claim_campaign("interrupted").startswith("execution-")
        assert registry.list_trials("interrupted") == []
    with Registry(database) as restarted:
        assert restarted.create_campaign({"campaign_id": "interrupted"}) == "interrupted"
        with pytest.raises(ValueError, match="already claimed"):
            restarted.claim_campaign("interrupted")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            restarted.connection.execute("DELETE FROM execution_claims")


def test_campaign_claim_is_atomic_across_independent_connections(tmp_path):
    database = tmp_path / "registry.sqlite"
    with Registry(database) as registry:
        registry.create_campaign({"campaign_id": "exclusive"})

    def claim(_):
        with Registry(database) as registry:
            try:
                registry.claim_campaign("exclusive")
            except ValueError:
                return False
            return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(8)))
    assert claims.count(True) == 1


def test_schema_one_migrates_without_changing_historical_rows(tmp_path):
    database = tmp_path / "old.sqlite"
    migration = __import__("pathlib").Path(__file__).parents[1] / "src/harborforge/storage/migrations/001_registry.sql"
    with sqlite3.connect(database) as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?)",
                           ("old", "adversarial_discovery", "discovery", "terminal_agent", "digest", "{}", "before"))
    with Registry(database) as registry:
        assert registry.get_campaign("old")["created_utc"] == "before"
        registry.claim_campaign("old")
        assert registry.connection.execute("PRAGMA user_version").fetchone()[0] == 2
