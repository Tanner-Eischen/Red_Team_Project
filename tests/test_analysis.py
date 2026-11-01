import json

import pytest

from harborforge.analysis.failure_classifier import analyze_campaign, scan_trace
from harborforge.analysis.reporting import build_report
from harborforge.analysis.statistics import (
    completion_summary, paired_comparison, paired_completion_comparison, summarize_trials, wilson_interval,
)
from harborforge.storage import Registry
from harborforge.evaluation import freeze_suite

from test_registry import make_task, prepare


def attempts(statuses):
    return [{"task_version_id": task, "outcome": status, "repeat_index": index,
             "model_profile_id": "test-model", "harness_profile_id": "test-harness",
             "protocol": "terminal_agent", "cohort": "discovery"}
            for task, values in statuses.items() for index, status in enumerate(values)]


def test_wilson_bounds_and_empty():
    assert wilson_interval(0, 0) is None
    assert wilson_interval(0, 10)[0] == 0
    assert wilson_interval(10, 10)[1] == 1
    assert wilson_interval(5, 10) == pytest.approx([0.2365930905, 0.7634069095])
    with pytest.raises(ValueError):
        wilson_interval(2, 1)


def test_repeats_are_clustered_and_errors_retained():
    rows = attempts({"a": ["PASS"] * 8, "b": ["MODEL_FAIL"] * 2, "c": ["PROVIDER_ERROR"]})
    summary = summarize_trials(rows)
    assert summary["attempts"] == 11
    assert summary["eligible_trials"] == 10
    assert summary["excluded_trials"] == 1
    assert summary["trial_pass_rate"] == 0.8
    assert summary["task_mean_pass_rate"] == 0.5
    assert summary["interval_method"].startswith("task_cluster")
    assert summary["interval_95"] == [0, 1]


def test_single_repeated_task_does_not_invent_population_interval():
    summary = summarize_trials(attempts({"a": ["PASS", "MODEL_FAIL"]}))
    assert summary["eligible_tasks"] == 1
    assert summary["interval_95"] is None


def test_timeout_policy_and_complete_k_metrics():
    rows = attempts({"a": ["PASS", "PASS"], "b": ["PASS", "MODEL_FAIL"], "c": ["PASS", "PROVIDER_ERROR"],
                     "d": ["AGENT_TIMEOUT", "MODEL_FAIL"]})
    summary = summarize_trials(rows, expected_repeats=2)
    assert summary["complete_repeated_tasks"] == 2
    assert summary["empirical_pass_at_k"] == 1
    assert summary["empirical_pass_power_k"] == 0.5
    counted = summarize_trials(rows, include_timeouts=True, expected_repeats=2)
    assert counted["complete_repeated_tasks"] == 3
    assert counted["eligible_trials"] == summary["eligible_trials"] + 1


def test_paired_task_differences_and_harness_parity():
    a = attempts({"one": ["PASS", "PASS"], "two": ["PASS", "MODEL_FAIL"]})
    b = attempts({"one": ["MODEL_FAIL", "MODEL_FAIL"], "two": ["MODEL_FAIL", "MODEL_FAIL"], "extra": ["PASS"]})
    comparison = paired_comparison(a, b)
    assert comparison["mean_paired_difference"] == 0.75
    assert comparison["paired_tasks"] == 2
    assert comparison["b_unpaired_tasks"] == 1
    b[0]["harness_profile_id"] = "different"
    with pytest.raises(ValueError, match="harness"):
        paired_comparison(a, b)


def test_scanner_keeps_infra_separate_and_suggestions_unreviewed():
    findings = scan_trace("AssertionError: bad units\nKeyError: status", outcome="MODEL_FAIL")
    assert [item["line_number"] for item in findings] == [1, 2]
    assert all(not item["causal_claim"] and item["review_status"] == "unreviewed" for item in findings)
    infra = scan_trace("AssertionError: docker", outcome="INFRA_ERROR")
    assert infra[0]["category"] == "infra_error"
    assert scan_trace("AssertionError", outcome="PASS") == []


def test_empty_report_has_no_fabricated_scores(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        registry.create_campaign({"campaign_id": "empty"})
        path = build_report(registry, "empty", tmp_path / "report")
    assert "No model attempts have been recorded" in path.read_text(encoding="utf-8")
    bundle = json.loads((path.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["summaries"] == []
    assert bundle["cost"]["known_total_usd"] is None


def test_report_contains_complete_counts_escaped_data_and_event_exports(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        trial_id = registry.record_trial({**base, "repeat_index": 0, "outcome": "MODEL_FAIL", "reward": 0,
                                          "error_code": "<script>alert('x')</script>", "cost_usd": None})
        registry.append_event(trial_id, "verifier_result", {"text": "AssertionError: wrong null handling"})
        registry.record_trial({**base, "repeat_index": 1, "outcome": "PROVIDER_ERROR"})
        first_analysis = analyze_campaign(registry, "test-campaign")
        assert len(first_analysis) == 2
        assert analyze_campaign(registry, "test-campaign") == []
        path = build_report(registry, "test-campaign", tmp_path / "report")
    document = path.read_text(encoding="utf-8")
    assert "<script>alert" not in document
    assert "&lt;script&gt;" in document
    assert "Task × model outcomes" in document
    assert len(list((path.parent / "events").glob("*.jsonl"))) == 2
    bundle = json.loads((path.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["outcome_counts"] == {"MODEL_FAIL": 1, "PROVIDER_ERROR": 1}
    assert bundle["summaries"][0]["eligible_trials"] == 1
    assert bundle["summaries"][0]["excluded_trials"] == 1
    assert bundle["coverage"]["missing_slots"] == 0


def test_report_labels_local_controls_without_certifying_or_inventing_trials(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        task = registry.get_task(base["task_version_id"])
        registry.record_validation(base["task_version_id"], {
            "valid": True, "runtime": "local", "task_sha256": task["artifact_sha256"],
            "eligible_for_model_evaluation": False, "review_status": "pending",
            "gates": [{"name": "oracle_1", "passed": True}, {"name": "nop", "passed": True}],
        })
        output = build_report(registry, "test-campaign", tmp_path / "report")
    document = output.read_text(encoding="utf-8")
    assert "Local functional controls only" in document
    assert "cannot authorize model scoring" in document
    assert "oracle_1" in document
    bundle = json.loads((output.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["quality"]["local_validation_runs"] == 1
    assert bundle["quality"]["gate_execution_counts"]["nop"] == {"passed": 1, "executed": 1}
    assert bundle["trial_count"] == 0
    assert bundle["coverage"]["missing_slots"] == 2


def test_archived_trace_scanner_preserves_hash_line_and_unreviewed_status(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        item = registry.store_artifact(b"agent command\nAssertionError: nullable status discarded\n",
                                       artifact_type="trace", visibility="private")
        registry.record_trial({**base, "outcome": "MODEL_FAIL", "reward": 0,
                                "trace_files": [{"path": "verifier/stdout.txt", **item}]})
        analyze_campaign(registry, "test-campaign")
        findings = registry.list_findings("test-campaign")
        assert findings[0]["evidence_artifact_sha256"] == item["sha256"]
        assert findings[0]["line_number"] == 2
        assert findings[0]["category"] == "behavioral_assertion"
        assert findings[0]["review_status"] == "unreviewed"
        assert findings[0]["causal_claim"] is False


def test_archived_scanner_does_not_follow_live_paths_and_detects_corruption(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        item = registry.store_artifact(b"AssertionError: original\n", artifact_type="trace")
        registry.artifacts.path_for(item["sha256"]).write_bytes(b"AssertionError: changed\n")
        trial_id = registry.record_trial({**base, "outcome": "MODEL_FAIL", "reward": 0,
                                          "trace_files": [{"path": "../../private.txt", **item}]})
        analyze_campaign(registry, "test-campaign")
        assert any(event["event_type"] == "trace_evidence_unavailable" for event in registry.list_events(trial_id))
        assert registry.list_findings("test-campaign")[0]["category"] == "uncertain"


def test_fixed_report_recovers_missing_matrix_from_verified_suite(tmp_path):
    task, manifest = make_task(tmp_path, cohort="holdout")
    suite_path = tmp_path / "frozen.json"
    config = {"campaign_id": "fixed", "objective": "fixed_evaluation", "cohort": "holdout",
              "protocol": "terminal_agent", "tasks": [], "suite_manifest": str(suite_path),
              "model_profiles": [{"profile_id": "m", "provider": "recording", "model_id": "offline", "repeats": 3}],
              "harness_profile": {"profile_id": "h", "harbor_version": "0.13.2"}, "include_timeouts_in_denominator": True}
    freeze_suite([task], suite_path, config)
    with Registry(tmp_path / "registry.sqlite") as registry:
        registry.register_task(manifest, task)
        registry.create_campaign(config)
        report = build_report(registry, "fixed", tmp_path / "report")
        bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
        assert bundle["coverage"]["planning_source"] == "verified_frozen_suite"
        assert bundle["coverage"]["missing_slots"] == 3
        suite_path.unlink()
        report = build_report(registry, "fixed", tmp_path / "unavailable")
        bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
        assert bundle["coverage"]["planning_source"] == "unavailable_frozen_suite"
        assert bundle["coverage"]["missing_slots"] is None


def test_primary_completion_counts_timeouts_and_collapses_retries_without_hiding_errors():
    rows = attempts({"a": ["PASS", "AGENT_TIMEOUT"], "b": ["PROVIDER_ERROR"]})
    rows.append({**rows[-1], "attempt_number": 1, "outcome": "MODEL_FAIL"})
    rows.extend(attempts({"c": ["INFRA_ERROR"]}))
    summary = completion_summary(rows, expected_repeats=2, planned_task_ids=["a", "b", "c", "missing"])
    assert summary["task_mean_completion_success"] == 0.25  # mean(a=1/2,b=0/1), not pooled 1/3
    assert summary["eligible_final_slots"] == 3
    assert summary["excluded_final_slots"] == 1
    assert summary["retained_raw_attempts"] == 5 and summary["superseded_retry_attempts"] == 1
    assert summary["planned_slots"] == 8 and summary["missing_slots"] == 4
    assert summary["tasks_without_scorable_results"] == ["c", "missing"]
    assert summary["conditional_scored"]["eligible_slots"] == 2
    assert summary["conditional_scored"]["pass_rate"] == 0.5
    assert summary["task_details"]["a"]["complete_for_planned_repeats"]
    assert not summary["task_details"]["b"]["complete_for_planned_repeats"]


def test_primary_rejects_duplicate_slots_and_preserves_final_infrastructure_failure():
    first = attempts({"a": ["PASS"]})[0]
    with pytest.raises(ValueError, match="Duplicate"):
        completion_summary([first, first])
    last = {**first, "attempt_number": 1, "outcome": "PROVIDER_ERROR"}
    summary = completion_summary([first, last], expected_repeats=1)
    assert summary["task_mean_completion_success"] is None
    assert summary["excluded_final_slots"] == 1
    with pytest.raises(ValueError, match="positive integer"):
        completion_summary([], expected_repeats=True)
    unexpected = {**first, "repeat_index": 9}
    summary = completion_summary([unexpected], expected_repeats=1)
    assert summary["task_mean_completion_success"] is None
    assert summary["missing_slots"] == 1 and summary["out_of_plan_final_slots"] == 1
    assert summary["task_details"]["a"]["unexpected_repeats"] == [9]


def test_primary_paired_comparison_uses_complete_task_blocks():
    a = attempts({"one": ["PASS", "AGENT_TIMEOUT"], "two": ["PASS", "PASS"]})
    b = attempts({"one": ["MODEL_FAIL", "MODEL_FAIL"], "two": ["PASS", "PROVIDER_ERROR"]})
    result = paired_completion_comparison(a, b, a_repeats=2, b_repeats=2)
    assert result["paired_complete_tasks"] == 1
    assert result["mean_paired_completion_difference"] == 0.5
    assert result["task_differences"] == {"one": 0.5}
    assert result["b_unpaired_or_incomplete_tasks"] == ["two"]
    missing = paired_completion_comparison(a, [], a_repeats=2, b_repeats=2)
    assert missing["paired_complete_tasks"] == 0
    assert missing["b_unpaired_or_incomplete_tasks"] == ["one", "two"]


def test_report_retains_unregistered_planned_tasks_and_unattempted_models(tmp_path):
    path, manifest = make_task(tmp_path)
    config = {"campaign_id": "planned", "cohort": "discovery", "protocol": "terminal_agent",
              "tasks": [str(path)], "harness_profile": {"profile_id": "fixed-harness"},
              "model_profiles": [{"profile_id": "a", "repeats": 2}, {"profile_id": "b", "repeats": 3}]}
    with Registry(tmp_path / "registry.sqlite") as registry:
        registry.create_campaign(config)
        report = build_report(registry, "planned", tmp_path / "report")
    bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["coverage"]["planned_slots"] == 5
    assert bundle["coverage"]["missing_slots"] == 5
    assert bundle["coverage"]["planned_base_tasks"] == 1
    assert len(bundle["summaries"]) == 2
    assert all(item["primary_completion"]["task_mean_completion_success"] is None for item in bundle["summaries"])
    document = report.read_text(encoding="utf-8")
    assert manifest["task_version_id"] in document
    assert "<th>a</th><th>b</th>" in document
    assert document.count(">Missing</td>") == 2
    assert bundle["evidence_level"] == "local_controls_or_empty_no_model_attempts"


def test_missing_configured_task_is_unknown_planning_not_zero(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        registry.create_campaign({"campaign_id": "missing", "tasks": [str(tmp_path / "not-there")]})
        report = build_report(registry, "missing", tmp_path / "report")
    bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["coverage"]["planned_slots"] is None
    assert bundle["coverage"]["missing_slots"] is None
    assert bundle["coverage"]["planning_source"] == "unavailable_configured_tasks"


def test_public_report_rejects_holdout_before_writing_and_labels_recording_rows(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        registry.create_campaign({"campaign_id": "private", "cohort": "holdout", "objective": "fixed_evaluation"})
        with pytest.raises(ValueError, match="private holdout"):
            build_report(registry, "private", tmp_path / "forbidden", public=True)
        assert not (tmp_path / "forbidden").exists()
        base = prepare(registry, tmp_path)
        registry.record_trial({**base, "outcome": "PROVIDER_ERROR"})
        report = build_report(registry, "test-campaign", tmp_path / "public", public=True)
    bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["export_visibility"] == "public_discovery"
    assert bundle["evidence_level"] == "recorded_attempts_execution_provenance_unverified"


def test_offline_contract_attempts_never_become_real_model_evidence(tmp_path):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        registry.record_trial({**base, "outcome": "PASS", "reward": 1, "execution_mode": "offline_contract_test"})
        report = build_report(registry, "test-campaign", tmp_path / "report")
    bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["trial_count"] == 1
    assert bundle["evidence_level"] == "offline_contract_tests_no_model_measurement"
    assert bundle["execution_mode_counts"] == {"offline_contract_test": 1}
    assert "no real model attempts have been recorded" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("malformed_mode", [None, {"untrusted": "metadata"}])
def test_report_retains_attempts_with_unknown_execution_metadata(tmp_path, malformed_mode):
    with Registry(tmp_path / "registry.sqlite") as registry:
        base = prepare(registry, tmp_path)
        registry.record_trial({**base, "outcome": "PROVIDER_ERROR", "execution_mode": malformed_mode})
        registry.record_trial({**base, "repeat_index": 1, "outcome": "PROVIDER_ERROR",
                               "execution_mode": "offline_contract_test"})
        report = build_report(registry, "test-campaign", tmp_path / "report")
    bundle = json.loads((report.parent / "summary.json").read_text(encoding="utf-8"))
    assert bundle["trial_count"] == 2
    assert bundle["execution_mode_counts"] == {"offline_contract_test": 1, "unrecorded": 1}
    assert bundle["evidence_level"] == "mixed_execution_provenance_not_model_measurement"
