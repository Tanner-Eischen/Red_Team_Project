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

from .statistics import paired_comparison, summarize_trials


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
    return repeats if isinstance(repeats, int) and repeats > 0 else None


def build_report(registry, campaign_id: str, output_dir: Path | str) -> Path:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    campaign = registry.get_campaign(campaign_id)
    trials = registry.list_trials(campaign_id)
    findings = registry.list_findings(campaign_id)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for trial in trials:
        groups[(trial["protocol"], trial["cohort"], trial["harness_profile_id"],
                trial["family"], trial["model_profile_id"])].append(trial)
    include_timeouts = campaign.get("include_timeouts_in_denominator", campaign.get("include_timeouts", False))
    policy = campaign.get("measurement", {})
    if isinstance(policy, dict):
        include_timeouts = policy.get("include_timeouts", include_timeouts)
    summaries = []
    for (protocol, cohort, harness, family, model_id), group in sorted(groups.items()):
        summaries.append({"protocol": protocol, "cohort": cohort, "harness_profile_id": harness,
                          "family": family, "model_profile_id": model_id,
                          **summarize_trials(group, include_timeouts=include_timeouts,
                                             expected_repeats=_expected_repeats(campaign, group[0]["model"]))})
    paired = []
    comparison_groups: dict[tuple, dict[str, list[dict]]] = defaultdict(dict)
    for (protocol, cohort, harness, family, model_id), group in groups.items():
        comparison_groups[(protocol, cohort, harness, family)][model_id] = group
    for (protocol, cohort, harness, family), model_groups in sorted(comparison_groups.items()):
        for a, b in itertools.combinations(sorted(model_groups), 2):
            paired.append({"protocol": protocol, "cohort": cohort, "harness_profile_id": harness,
                           "family": family, "model_a": a, "model_b": b,
                           **paired_comparison(model_groups[a], model_groups[b], include_timeouts=include_timeouts)})
    lineage_comparisons = []
    eligible_outcomes = {"PASS", "MODEL_FAIL"} | ({"AGENT_TIMEOUT"} if include_timeouts else set())
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
                lineage_comparisons.append({"model_profile_id": model_id, "harness_profile_id": harness,
                                            "parent": parent_id, "child": child_id,
                                            "parent_pass_rate": a, "child_pass_rate": b,
                                            "child_minus_parent": b - a if a is not None and b is not None else None,
                                            "parent_attempts": parent_rate["attempts"],
                                            "child_attempts": child_rate["attempts"],
                                            "interval_95": None,
                                            "note": "One lineage pair; descriptive only, no independent-task interval"})
    task_id_set = {trial["task_version_id"] for trial in trials}
    declared_paths = {str(Path(path).resolve()) for path in campaign.get("tasks", [])}
    declared_task_ids = set()
    planning_source = "configured_base_tasks"
    planning_error = None
    if not declared_paths and campaign.get("objective") == "fixed_evaluation" and campaign.get("suite_manifest"):
        from harborforge.evaluation import verify_suite

        try:
            suite = verify_suite(campaign["suite_manifest"], json.loads(campaign["config_json"]))
            declared_paths = {str(Path(entry["task_path"]).resolve()) for entry in suite["tasks"]}
            declared_task_ids.update(entry["task_version_id"] for entry in suite["tasks"])
            planning_source = "verified_frozen_suite"
        except (OSError, ValueError, KeyError, TypeError) as error:
            planning_source = "unavailable_frozen_suite"
            planning_error = type(error).__name__ + ": " + str(error)
    for registered in registry.connection.execute("SELECT task_version_id, task_path FROM task_versions"):
        if registered["task_path"] in declared_paths:
            task_id_set.add(registered["task_version_id"])
            declared_task_ids.add(registered["task_version_id"])
    task_ids = sorted(task_id_set)
    tasks = [registry.get_task(task_id) for task_id in task_ids]
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
    models = campaign.get("model_profiles", [])
    planned_slots = {(task_id, model.get("profile_id", model.get("model_profile_id")), repeat)
                     for task_id in declared_task_ids for model in models for repeat in range(model.get("repeats", 1))}
    observed_slots = {(trial["task_version_id"], trial["model_profile_id"], trial.get("repeat_index", 0))
                      for trial in trials}
    bundle = redact({"schema_version": 1, "generated_utc": utc_now(), "campaign": campaign,
                     "trial_count": len(trials), "task_count": len(tasks),
                     "outcome_counts": dict(sorted(Counter(trial["outcome"] for trial in trials).items())),
                     "summaries": summaries, "paired_comparisons": paired,
                     "lineage_comparisons": lineage_comparisons, "tasks": tasks,
                     "coverage": {"planned_slots": len(planned_slots) if planning_error is None else None,
                                  "observed_slots": len(observed_slots),
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
                         "Trace exports may contain private task or user data; review before publishing.",
                     ]})
    (output / "summary.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                                         encoding="utf-8")
    (output / "trials.jsonl").write_text("".join(canonical_json(redact(trial)) + "\n" for trial in trials),
                                         encoding="utf-8")
    (output / "tasks.jsonl").write_text("".join(canonical_json(task) + "\n" for task in tasks), encoding="utf-8")
    columns = ["trial_id", "task_version_id", "family", "protocol", "cohort", "model_profile_id",
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
    rows = []
    for item in summaries:
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
        matrix_tasks = sorted({trial["task_version_id"] for group in model_groups.values() for trial in group})
        cells = []
        for task_id in matrix_tasks:
            row = f"<tr><th>{_escape(task_id)}</th>"
            for model_name in model_names:
                observed = [trial for trial in model_groups[model_name] if trial["task_version_id"] == task_id]
                eligible = [trial for trial in observed if trial["outcome"] in eligible_outcomes]
                passed = sum(trial["outcome"] == "PASS" for trial in eligible)
                label = f"{passed}/{len(eligible)}; {len(observed) - len(eligible)} excluded" if observed else "Missing"
                color = "#edf0f3" if not eligible else ("#d9f0e3" if passed == len(eligible) else "#ffe5dc")
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
    empty = "<p>No model attempts have been recorded. No performance scores are available.</p>" if not trials else ""
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{_escape(campaign_id)} — HarborForge evidence</title>
<style>body{{font:16px system-ui,sans-serif;margin:2rem auto;max-width:1400px;padding:0 1rem;color:#17202a;background:#f7f9fb}}
table{{border-collapse:collapse;width:100%;font-size:.9rem;background:white}}td,th{{padding:.65rem;text-align:left;border-bottom:1px solid #dae1e8}}
th{{background:#e9eff5}}code{{overflow-wrap:anywhere}}section{{margin:2rem 0;overflow-x:auto}}.note{{padding:1rem;border-left:4px solid #ad7412;background:#fff6e1}}
a{{color:#1258a0}}h1{{letter-spacing:-.03em}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style></head>
<body><h1>HarborForge evaluation evidence</h1><p>Campaign: <strong>{_escape(campaign_id)}</strong> ·
{len(tasks)} observed tasks · {len(trials)} attempts · {_escape(campaign['protocol'])} · {_escape(campaign['cohort'])}</p>
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
<section><h2>Model results by family and harness</h2><p>Intervals use task clusters for repeated attempts.
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
