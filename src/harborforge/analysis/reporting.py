"""Static evidence bundle; reports contain only recorded attempts and explicit missingness."""

from __future__ import annotations

import csv
import hashlib
import html
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

from harborforge.storage.registry import canonical_json, redact, utc_now

from .statistics import completion_summary, paired_comparison, paired_completion_comparison, summarize_trials


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _csv_value(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _rate(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value:.1%}"


def _expected_repeats(campaign: dict, model: dict) -> int | None:
    repeats = model.get("repeats", campaign.get("repeats"))
    return repeats if type(repeats) is int and repeats > 0 else None


def _planned_tasks(registry, campaign: dict) -> tuple[dict[str, dict], str, str | None]:
    """Resolve the declared matrix even before the first task is registered."""
    from harborforge.authoring.provenance import read_manifest

    source = "configured_base_tasks"
    try:
        paths = campaign.get("tasks", [])
        if campaign.get("objective") == "fixed_evaluation" and campaign.get("suite_manifest"):
            from harborforge.evaluation import verify_suite

            suite = verify_suite(campaign["suite_manifest"], json.loads(campaign["config_json"]))
            paths = [entry["task_path"] for entry in suite["tasks"]]
            source = "verified_frozen_suite"
        registered = {row["task_path"]: dict(row) for row in registry.connection.execute("SELECT * FROM task_versions")}
        tasks = {}
        for value in paths:
            path = str(Path(value).resolve())
            if path in registered:
                task = registered[path]
            else:
                manifest = read_manifest(path)
                task = {**manifest, "task_path": path, "manifest": manifest,
                        "parent_task_version_id": manifest.get("parent_task_version_id")}
            task_id = task["task_version_id"]
            if task_id in tasks:
                raise ValueError("Duplicate configured task version")
            if task["cohort"] != campaign.get("cohort", "discovery") or task["protocol"] != campaign.get(
                "protocol", "terminal_agent"
            ):
                raise ValueError("Configured task cohort/protocol differs from campaign")
            tasks[task_id] = task
        return tasks, source, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {}, "unavailable_frozen_suite" if source == "verified_frozen_suite" or campaign.get(
            "suite_manifest"
        ) else "unavailable_configured_tasks", type(error).__name__ + ": " + str(error)


def _execution_mode(trial: dict) -> str:
    value = trial.get("execution_mode")
    return value if isinstance(value, str) and value.strip() else "unrecorded"


def _evidence_level(trials: list[dict], validations: list[dict]) -> str:
    if trials:
        modes = {_execution_mode(trial) for trial in trials}
        if modes == {"offline_contract_test"}:
            return "offline_contract_tests_no_model_measurement"
        if "offline_contract_test" in modes:
            return "mixed_execution_provenance_not_model_measurement"
        if modes == {"harbor_terminal_runtime"}:
            return "harbor_terminal_attempts_requires_provider_trace_review"
        # A registry row or an injected adapter is not proof of a real provider
        # attempt. A runtime receipt must explicitly attest independent execution.
        if all(isinstance(trial.get("execution_evidence"), dict) and trial["execution_evidence"].get(
            "kind"
        ) == "real_model_trial" for trial in trials):
            return "declared_real_model_trials_requires_trace_review"
        return "recorded_attempts_execution_provenance_unverified"
    if any(item["valid"] and item["evidence"].get("runtime") == "docker" and item["evidence"].get(
        "eligible_for_model_evaluation"
    ) is True for item in validations):
        return "container_controls_no_model_attempts"
    return "local_controls_or_empty_no_model_attempts"


def build_report(registry, campaign_id: str, output_dir: Path | str, *, public: bool = False) -> Path:
    output = Path(output_dir).resolve()
    campaign = registry.get_campaign(campaign_id)
    if public and campaign.get("cohort", "discovery") != "discovery":
        raise ValueError("Public exports cannot include calibration or private holdout task records")
    trials = registry.list_trials(campaign_id)
    findings = registry.list_findings(campaign_id)
    planned_tasks, planning_source, planning_error = _planned_tasks(registry, campaign)
    declared_task_ids = set(planned_tasks)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for trial in trials:
        groups[(trial["protocol"], trial["cohort"], trial["harness_profile_id"],
                trial["family"], trial["model_profile_id"])].append(trial)
    models = campaign.get("model_profiles", [])
    configured_models = {model.get("profile_id", model.get("model_profile_id")): model for model in models}
    for task in planned_tasks.values():
        for model_id in configured_models:
            harness_id = campaign.get("harness_profile", {}).get("profile_id")
            if harness_id is None:
                existing = {key[2] for key in groups if key[4] == model_id}
                harness_id = next(iter(existing)) if len(existing) == 1 else "unregistered-harness"
            groups.setdefault((task["protocol"], task["cohort"], harness_id, task["family"], model_id), [])
    include_timeouts = campaign.get("include_timeouts_in_denominator", campaign.get("include_timeouts", False))
    policy = campaign.get("measurement", {})
    if isinstance(policy, dict):
        include_timeouts = policy.get("include_timeouts", include_timeouts)
    summaries = []
    for (protocol, cohort, harness, family, model_id), group in sorted(groups.items()):
        model = group[0]["model"] if group else configured_models[model_id]
        expected_repeats = _expected_repeats(campaign, model)
        summaries.append({"protocol": protocol, "cohort": cohort, "harness_profile_id": harness,
                          "family": family, "model_profile_id": model_id,
                          **summarize_trials(group, include_timeouts=include_timeouts,
                                             expected_repeats=expected_repeats),
                          "primary_completion": completion_summary(
                              group, expected_repeats=expected_repeats,
                              planned_task_ids=[task_id for task_id, task in planned_tasks.items()
                                                if task["family"] == family])})
    paired = []
    comparison_groups: dict[tuple, dict[str, list[dict]]] = defaultdict(dict)
    for (protocol, cohort, harness, family, model_id), group in groups.items():
        comparison_groups[(protocol, cohort, harness, family)][model_id] = group
    for (protocol, cohort, harness, family), model_groups in sorted(comparison_groups.items()):
        for a, b in itertools.combinations(sorted(model_groups), 2):
            paired.append({"protocol": protocol, "cohort": cohort, "harness_profile_id": harness,
                           "family": family, "model_a": a, "model_b": b,
                           **paired_comparison(model_groups[a], model_groups[b], include_timeouts=include_timeouts),
                           "primary_completion": paired_completion_comparison(
                               model_groups[a], model_groups[b],
                               a_repeats=_expected_repeats(campaign, configured_models.get(a, {})),
                               b_repeats=_expected_repeats(campaign, configured_models.get(b, {})),
                               planned_task_ids=[task_id for task_id, task in planned_tasks.items()
                                                 if task["family"] == family])})
    lineage_comparisons = []
    for (protocol, cohort, harness, family, model_id), group in sorted(groups.items()):
        by_task: dict[str, list[dict]] = defaultdict(list)
        for trial in group:
            by_task[trial["task_version_id"]].append(trial)
        for child_id, child_trials in by_task.items():
            parent_id = child_trials[0].get("parent_task_version_id")
            if parent_id and parent_id in by_task:
                parent_rate = summarize_trials(by_task[parent_id], include_timeouts=include_timeouts)
                child_rate = summarize_trials(child_trials, include_timeouts=include_timeouts)
                a, b = parent_rate["task_mean_pass_rate"], child_rate["task_mean_pass_rate"]
                primary_a = completion_summary(by_task[parent_id])["task_mean_completion_success"]
                primary_b = completion_summary(child_trials)["task_mean_completion_success"]
                lineage_comparisons.append({"model_profile_id": model_id, "harness_profile_id": harness,
                                            "parent": parent_id, "child": child_id,
                                            "parent_pass_rate": a, "child_pass_rate": b,
                                            "child_minus_parent": b - a if a is not None and b is not None else None,
                                            "parent_attempts": parent_rate["attempts"],
                                            "child_attempts": child_rate["attempts"],
                                            "primary_parent_completion_rate": primary_a,
                                            "primary_child_completion_rate": primary_b,
                                            "primary_child_minus_parent": primary_b - primary_a
                                            if primary_a is not None and primary_b is not None else None,
                                            "interval_95": None,
                                            "note": "One lineage pair; descriptive only, no independent-task interval"})
    task_id_set = {trial["task_version_id"] for trial in trials}
    task_id_set.update(declared_task_ids)
    task_ids = sorted(task_id_set)
    registered_ids = {row["task_version_id"] for row in registry.connection.execute("SELECT task_version_id FROM task_versions")}
    tasks = [registry.get_task(task_id) if task_id in registered_ids else planned_tasks[task_id] for task_id in task_ids]
    if public and any(task["cohort"] != "discovery" for task in tasks):
        raise ValueError("Public exports cannot include calibration or private holdout task records")
    validations = []
    for task_id in task_ids:
        validations.extend([{**dict(row), "evidence": json.loads(row["validation_json"])}
                            for row in registry.connection.execute(
                                "SELECT * FROM validation_runs WHERE task_version_id = ? ORDER BY created_utc",
                                (task_id,))])
    quality_gate_counts: dict[str, dict[str, int]] = {}
    for validation in validations:
        for gate in validation["evidence"].get("gates", []):
            name = str(gate.get("name", "unnamed"))
            counts = quality_gate_counts.setdefault(name, {"passed": 0, "executed": 0})
            counts["executed"] += 1
            counts["passed"] += gate.get("passed") is True
    known_cost = [trial["cost_usd"] for trial in trials if trial.get("cost_usd") is not None]
    planned_slots = {(task_id, model.get("profile_id", model.get("model_profile_id")), repeat)
                     for task_id in declared_task_ids for model in models
                     for repeat in range(_expected_repeats(campaign, model) or 1)}
    observed_slots = {(trial["task_version_id"], trial["model_profile_id"], trial.get("repeat_index", 0))
                      for trial in trials}
    evidence_level = _evidence_level(trials, validations)
    bundle = redact({"schema_version": 1, "generated_utc": utc_now(), "campaign": campaign,
                     "evidence_level": evidence_level, "export_visibility": "public_discovery" if public else "private_review_required",
                     "trial_count": len(trials), "task_count": len(tasks),
                     "execution_mode_counts": dict(sorted(Counter(
                         _execution_mode(trial) for trial in trials).items())),
                     "outcome_counts": dict(sorted(Counter(trial["outcome"] for trial in trials).items())),
                     "summaries": summaries, "paired_comparisons": paired,
                     "lineage_comparisons": lineage_comparisons, "tasks": tasks,
                     "coverage": {"planned_slots": len(planned_slots) if planning_error is None else None,
                                  "observed_slots": len(observed_slots),
                                  "observed_planned_slots": len(observed_slots & planned_slots),
                                  "unexpected_slots": sorted(observed_slots - planned_slots),
                                  "planned_base_tasks": len(declared_task_ids),
                                  "observed_revision_tasks": len(task_id_set - declared_task_ids),
                                  "planning_note": "Base-task matrix; adaptive child trials are observed separately. "
                                  "Tiered discovery may intentionally stop before all base slots are attempted.",
                                  "planning_source": planning_source, "planning_error": planning_error,
                                  "missing_slots": len(planned_slots - observed_slots) if planning_error is None else None,
                                  "missing": sorted(planned_slots - observed_slots) if planning_error is None else None},
                     "validations": validations, "findings": findings,
                     "quality": {"local_validation_runs": sum(
                         item["evidence"].get("runtime") == "local" for item in validations),
                         "eligible_docker_validation_runs": sum(
                             item["valid"] and item["evidence"].get("runtime") == "docker"
                             and item["evidence"].get("eligible_for_model_evaluation") is True
                             for item in validations), "gate_execution_counts": quality_gate_counts},
                     "cost": {"known_total_usd": sum(known_cost) if known_cost else None,
                              "known_attempts": len(known_cost), "unknown_attempts": len(trials) - len(known_cost)},
                     "limitations": [
                         "Discovery outcomes are conditional on adaptive selection and are not general capability estimates.",
                         "Public task publication prevents future claims of an untouched holdout.",
                         "Model identity aliases may drift; inspect exact requested and returned metadata.",
                         "Repeated attempts are clustered by task. Small-sample intervals are exploratory.",
                         "Error outcomes remain visible and are excluded from correctness denominators.",
                         "Primary completion always includes timeouts as unsuccessful and collapses retries to final repeat outcomes.",
                         "Legacy conditional rates and raw retry counts are retained separately from the primary metric.",
                         "Empirical repeated success fields are descriptive, not an unbiased pass@k estimator.",
                         "A registry attempt without trusted runtime provenance is not a measured real-model result.",
                         "Trace exports may contain private task or user data; review before publishing.",
                     ]})
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                                         encoding="utf-8")
    (output / "trials.jsonl").write_text("".join(canonical_json(redact(trial)) + "\n" for trial in trials),
                                         encoding="utf-8")
    (output / "tasks.jsonl").write_text("".join(canonical_json(task) + "\n" for task in tasks), encoding="utf-8")
    columns = ["trial_id", "task_version_id", "family", "protocol", "cohort", "model_profile_id", "execution_mode",
               "harness_profile_id", "repeat_index", "attempt_number", "outcome", "reward", "validation_id",
               "tokens_input", "tokens_output", "cost_usd", "wall_time_s", "error_code", "artifact_sha256"]
    with (output / "trials.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for trial in trials:
            writer.writerow({column: _csv_value(trial.get(column)) for column in columns})
    evidence_links = {}
    for trial in trials:
        safe_name = hashlib.sha256(trial["trial_id"].encode()).hexdigest()[:24]
        filename = f"events/{safe_name}.jsonl"
        registry.export_events(trial["trial_id"], output / filename)
        evidence_links[trial["trial_id"]] = filename
    rows, completion_rows = [], []
    for item in summaries:
        primary = item["primary_completion"]
        completion_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [
            item["model_profile_id"], item["family"], _rate(primary["task_mean_completion_success"]),
            f'{primary["passes"]}/{primary["eligible_final_slots"]}', primary["excluded_final_slots"],
            primary["missing_slots"], primary["complete_tasks"],
            _rate(primary["conditional_scored"]["pass_rate"]),
        ]) + "</tr>")
        interval = item["interval_95"]
        interval_text = "Unavailable" if interval is None else f"{_rate(interval[0])} – {_rate(interval[1])}"
        rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [
            item["model_profile_id"], item["family"], item["protocol"], item["cohort"], item["harness_profile_id"],
            f'{item["passes"]}/{item["eligible_trials"]}', item["excluded_trials"], item["eligible_tasks"],
            _rate(item["task_mean_pass_rate"]), interval_text,
        ]) + "</tr>")
    trial_rows = []
    for trial in trials:
        trial_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [
            trial["task_version_id"], trial["model_profile_id"], trial.get("repeat_index", 0),
            trial.get("attempt_number", 0), trial["outcome"], trial.get("reward"), trial.get("error_code") or "",
        ]) + f'<td><a href="{_escape(evidence_links[trial["trial_id"]])}">Events</a></td></tr>')
    lineage = "".join(f"<li>{_escape(task['task_version_id'])} &larr; "
                       f"{_escape(task['parent_task_version_id'] or 'original')} "
                       f"<code>{_escape(task['artifact_sha256'])}</code></li>" for task in tasks)
    matrices = []
    for (protocol, cohort, harness, family), model_groups in sorted(comparison_groups.items()):
        model_names = sorted(model_groups)
        matrix_tasks = sorted({trial["task_version_id"] for group in model_groups.values() for trial in group}
                              | {task_id for task_id, task in planned_tasks.items()
                                 if task["family"] == family and task["protocol"] == protocol})
        cells = []
        for task_id in matrix_tasks:
            row = f"<tr><th>{_escape(task_id)}</th>"
            for model_name in model_names:
                observed = [trial for trial in model_groups[model_name] if trial["task_version_id"] == task_id]
                cell = completion_summary(observed, expected_repeats=_expected_repeats(
                    campaign, configured_models.get(model_name, {})), planned_task_ids=[task_id])
                passed, eligible = cell["passes"], cell["eligible_final_slots"]
                label = (f"{passed}/{eligible}; {cell['excluded_final_slots']} excluded; {cell['missing_slots']} missing" if observed
                         else "Missing" if task_id in declared_task_ids else "Not scheduled (adaptive)")
                color = "#edf0f3" if not eligible else ("#d9f0e3" if passed == eligible else "#ffe5dc")
                row += f'<td style="background:{color}">{_escape(label)}</td>'
            cells.append(row + "</tr>")
        matrices.append(f"<h3>{_escape(family)} · {_escape(protocol)} · {_escape(cohort)} · {_escape(harness)}</h3>"
                        "<table><thead><tr><th>Task</th>" + "".join(f"<th>{_escape(name)}</th>" for name in model_names)
                        + "</tr></thead><tbody>" + "".join(cells) + "</tbody></table>")
    finding_rows = []
    for finding in findings:
        decisions = ", ".join(f"{review['reviewer']}: {review['decision']}" for review in finding["reviews"])
        finding_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [
            finding["trial_id"], finding["category"], finding.get("evidence", ""), decisions or "Unreviewed hypothesis",
        ]) + "</tr>")
    quality_rows = []
    for validation in validations:
        evidence = validation["evidence"]
        checks = evidence.get("gates", [])
        passed = sum(gate.get("passed") is True for gate in checks)
        runtime = evidence.get("runtime", "unknown")
        eligibility = validation["valid"] and runtime == "docker" and evidence.get("eligible_for_model_evaluation") is True
        review = evidence.get("review_status", evidence.get("reviewer_status", "pending"))
        scope = "Local functional controls only" if runtime == "local" else "Container controls" if eligibility else "Unverified"
        quality_rows.append("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [
            validation["task_version_id"], runtime, scope, "yes" if validation["valid"] else "no",
            f"{passed}/{len(checks)}", "yes" if eligibility else "no", review,
        ]) + "</tr>")
    gate_rows = "".join("<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in [
        name, item["passed"], item["executed"],
    ]) + "</tr>" for name, item in sorted(quality_gate_counts.items()))
    statuses = "".join(f"<li>{_escape(status)}: {count}</li>" for status, count in bundle["outcome_counts"].items())
    empty = ("<p>No model attempts have been recorded. No performance scores are available.</p>" if not trials else
             "<p>Offline contract-test attempts only; no real model attempts have been recorded.</p>"
             if evidence_level == "offline_contract_tests_no_model_measurement" else "")
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{_escape(campaign_id)} — HarborForge evidence</title>
<style>body{{font:16px system-ui,sans-serif;margin:2rem auto;max-width:1400px;padding:0 1rem;color:#17202a;background:#f7f9fb}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;background:white}}td,th{{padding:.65rem;text-align:left;border-bottom:1px solid #dae1e8}}
th{{background:#e9eff5}}code{{overflow-wrap:anywhere}}section{{margin:2rem 0;overflow-x:auto}}.note{{padding:1rem;border-left:4px solid #ad7412;background:#fff6e1}}
a{{color:#1258a0}}h1{{letter-spacing:-.03em}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style></head>
<body><h1>HarborForge evaluation evidence</h1><p>Campaign: <strong>{_escape(campaign_id)}</strong> ·
{len(tasks)} declared/observed task versions · {len(trials)} attempts · {_escape(campaign['protocol'])} · {_escape(campaign['cohort'])}</p>
<p class="note">Evidence level: <strong>{_escape(evidence_level)}</strong>.
Export visibility: {_escape(bundle['export_visibility'])}. Registry rows alone do not establish real-model execution.</p>
<p class="note">Exploratory evidence. Discovery results reflect adaptive selection. These results describe the configured model,
agent, tools, task collection and budgets. Review private traces before publishing.</p>{empty}
<section><h2>Complete outcome counts</h2><ul>{statuses or '<li>No attempts</li>'}</ul></section>
<section><h2>Task quality gates</h2><p class="note">Local controls prove functional behavior in fresh local workspaces.
They do not certify Docker, privilege, network or hidden-verifier isolation and cannot authorize model scoring.
Human review status is recorded separately from automated controls.</p>
<p>{bundle['quality']['local_validation_runs']} local validation runs;
{bundle['quality']['eligible_docker_validation_runs']} eligible Docker validation runs.</p>
<table><thead><tr><th>Task</th><th>Runtime</th><th>Scope</th><th>Controls valid</th><th>Gates passed / run</th>
<th>Eligible for model evaluation</th><th>Review</th></tr></thead><tbody>{''.join(quality_rows)}</tbody></table>
<h3>Control execution counts</h3><table><thead><tr><th>Gate</th><th>Passed</th><th>Executed</th></tr></thead>
<tbody>{gate_rows}</tbody></table></section>
<section><h2>Primary completion within budget</h2><p>Task-weighted success uses the final attempt in each task/repeat slot.
Agent timeouts count as unsuccessful. Provider, infrastructure and unscorable slots remain explicit coverage gaps;
tasks without scorable results are not silently counted as capability failures. No population interval is claimed.
Conditional correctness (final PASS / final PASS plus MODEL_FAIL) is shown separately.</p>
<table><thead><tr><th>Model</th><th>Family</th><th>Task-weighted completion</th><th>Pass / eligible final slots</th>
<th>Excluded final slots</th><th>Missing slots</th><th>Complete task blocks</th><th>Conditional correctness</th>
</tr></thead><tbody>{''.join(completion_rows)}</tbody></table></section>
<section><h2>Recorded profile outcomes by family and harness</h2><p>Intervals use task clusters for repeated attempts.
This legacy configured-score table retains raw attempts and its historical timeout policy; it is not the primary completion metric.
The pass rate shown is the mean task success fraction. Raw eligible-trial numerator/denominator and all excluded attempts are retained.
Timeouts count as {'failures' if include_timeouts else 'separate excluded outcomes'}.</p>
<table><thead><tr><th>Model</th><th>Family</th><th>Protocol</th><th>Split</th><th>Harness</th><th>Pass / eligible</th>
<th>Excluded</th><th>Independent tasks</th><th>Task mean</th><th>95% interval</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section><h2>All attempts and trace events</h2><table><thead><tr><th>Task</th><th>Model</th><th>Repeat</th><th>Retry</th>
<th>Outcome</th><th>Reward</th><th>Error</th><th>Evidence</th></tr></thead><tbody>{''.join(trial_rows)}</tbody></table></section>
<section><h2>Task × model outcomes</h2><p>{bundle['coverage']['missing_slots'] if planning_error is None else 'Unknown number of'} configured base task/model/repeat slots have no recorded attempt.
Adaptive child outcomes appear separately; discovery tier selection can intentionally leave base slots unattempted.</p>
<p>Planning source: {_escape(planning_source)}{'; matrix unavailable: ' + _escape(planning_error) if planning_error else ''}.</p>
{''.join(matrices)}</section>
<section><h2>Immutable task lineage</h2><ul>{lineage or '<li>No evaluated tasks</li>'}</ul></section>
<section><h2>Failure hypotheses and adjudication</h2><table><thead><tr><th>Trial</th><th>Category</th><th>Evidence</th><th>Reviews</th>
</tr></thead><tbody>{''.join(finding_rows)}</tbody></table></section>
<section><h2>Reproducibility bundle</h2><p><a href="summary.json">Campaign, quality controls, model/harness snapshots,
paired comparisons and uncertainty</a> · <a href="trials.csv">All attempts CSV</a> · <a href="trials.jsonl">All attempts JSONL</a>
· <a href="tasks.jsonl">Task manifests</a></p><p>Known cost: {_escape(bundle['cost']['known_total_usd'])} USD;
{bundle['cost']['unknown_attempts']} attempts have unknown cost. Unknown usage is not interpreted as zero.</p>
<p>Configuration SHA-256: <code>{_escape(campaign['config_sha256'])}</code></p></section>
<section><h2>Limitations</h2><ul>{''.join(f'<li>{_escape(note)}</li>' for note in bundle['limitations'])}</ul></section>
</body></html>'''
    path = output / "index.html"
    path.write_text(document, encoding="utf-8")
    return path
