"""Rebuild the public free-model case study and a local reference replay; no API calls."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import difflib
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from harborforge.authoring.provenance import read_manifest, task_digest
from harborforge.validation.quality_gates import _local_controls

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = ("hf02-free-openrouter-2026-11-02.json", "hf02-flint-openrouter-2026-11-05.json")
LABELS = {"openrouter/cohere/north-mini-code:free": "North Mini Code",
          "openrouter/nvidia/nemotron-3.5-super-vl:free": "Nemotron 3.5 Super VL",
          "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": "Nemotron 3 Ultra",
          "openrouter/recursive/flint-v1.0:free": "Flint"}
OUTCOMES = {"PASS", "MODEL_FAIL", "AGENT_TIMEOUT", "INFRA_ERROR", "PROVIDER_ERROR", "UNSCORABLE"}


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid nonnegative count")
    return value


def _zero(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value != 0:
        raise ValueError("Missing or nonzero known model cost")


def _outcomes(value, attempts):
    if not isinstance(value, dict) or set(value) - OUTCOMES:
        raise ValueError("Invalid outcome categories")
    if sum(_count(n) for n in value.values()) != attempts:
        raise ValueError("Outcome denominator does not match attempts")
    return dict(sorted(value.items()))


def read_results(root: Path) -> dict:
    """Read only the two public aggregate receipts, projecting explicit fields."""
    sources = []
    receipts = []
    for name in RECEIPTS:
        payload = (root / "docs/evidence" / name).read_bytes()
        receipt = json.loads(payload)
        if receipt.get("all_provider_receipts_known_and_zero") is not True:
            raise ValueError("Missing verified zero-cost accounting proof")
        _zero(receipt.get("cost_usd"))
        for field in ("planned_trials", "observed_trials", "unrun_trials"):
            _count(receipt.get(field))
        if (receipt.get("planned_trials") != receipt.get("observed_trials")
                or receipt.get("unrun_trials") != 0):
            raise ValueError("Incomplete trial denominator")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", receipt.get("date", "")):
            raise ValueError("Invalid study date")
        if _count(receipt.get("actual_provider_requests")) > _count(receipt.get("provider_request_ceiling")):
            raise ValueError("Provider requests exceed registered ceiling")
        receipts.append(receipt)
        sources.append({"file": name, "sha256": hashlib.sha256(payload).hexdigest(), "date": receipt["date"]})
    initial, flint = receipts
    if flint.get("model_id") != "openrouter/recursive/flint-v1.0:free":
        raise ValueError("Supplement must identify Flint")
    if (flint.get("reference_campaign_id") != initial.get("campaign_id")
            or flint.get("exact_reference_tasks_reused") is not True
            or flint.get("harness_and_sampling_matched") is not True):
        raise ValueError("Flint reference tasks/harness are not matched")
    harness = initial.get("snapshot_sha256", {}).get("harness-provenance.json")
    if (not isinstance(harness, str) or not re.fullmatch(r"[0-9a-f]{64}", harness)
            or flint.get("snapshots_sha256", {}).get("harness-provenance.json") != harness):
        raise ValueError("Mismatched harness provenance hash")
    base = initial.get("model_results")
    if not isinstance(base, list) or len(base) != 3:
        raise ValueError("Initial study must contain three model profiles")
    models = []
    for row, date in [(row, initial["date"]) for row in base] + [(flint, flint["date"])]:
        model = row.get("model_id")
        attempts = row.get("attempts", row.get("observed_trials"))
        if model not in LABELS or type(attempts) is not int or attempts != 6:
            raise ValueError("Expected six registered attempts per named model")
        _zero(row.get("known_cost_usd") if "attempts" in row else row.get("cost_usd"))
        outcomes = _outcomes(row.get("outcomes"), attempts)
        models.append({"model": LABELS[model], "model_id": model, "date": date, "attempts": attempts,
                       "passes": outcomes.get("PASS", 0), "outcomes": outcomes,
                       "completion_rate": outcomes.get("PASS", 0) / attempts, "cost_usd": 0,
                       "input_tokens": _count(row.get("native_input_tokens")),
                       "output_tokens": _count(row.get("native_output_tokens"))})
    if len({row["model_id"] for row in models}) != 4 or initial["observed_trials"] != sum(m["attempts"] for m in models[:3]):
        raise ValueError("Model identities or study denominator differ")
    totals = Counter()
    for model in models:
        totals.update(model["outcomes"])
    probe = initial.get("availability_probes", {})
    _zero(probe.get("reported_cost_usd"))
    extra = flint.get("availability_probe", {})
    _zero(extra.get("cost_usd"))
    if _count(probe.get("completed")) != 3 or _count(extra.get("requests")) != 1:
        raise ValueError("Availability probe denominator differs from the fixed study")
    if extra.get("status") != "passed":
        raise ValueError("Missing successful Flint availability proof")
    return {"schema_version": 1, "study": "Free OpenRouter terminal-agent calibration",
            "cohort": "calibration", "task_templates": 3, "repeats_per_model_task": 2,
            "trial_count": sum(m["attempts"] for m in models), "outcomes": dict(sorted(totals.items())),
            "trial_provider_requests": sum(_count(r.get("actual_provider_requests")) for r in receipts),
            "availability_provider_requests": _count(probe.get("completed")) + _count(extra.get("requests")),
            "earlier_protocol_development_requests_excluded": _count(initial.get(
                "provider_quota_reconciliation", {}).get("prior_protocol_development_requests")), "cost_usd": 0,
            "new_model_calls": 0, "models": models, "sources": sources,
            "harness_provenance_sha256": harness,
            "limits": ["Three calibration templates, two repeats per model; descriptive only, no holdout or universal ranking.",
                       "Flint ran November 5; the other models ran November 2. Tasks and harness matched across dates.",
                       "AGENT_TIMEOUT includes token/output stops; no causal failure mechanism is inferred from that category.",
                       "The public sample is an author/reference replay, not a model response or new capability measurement."]}


def reference_sample(root: Path, output: Path) -> dict:
    """Execute only already-public trusted author code in a disposable workspace."""
    task = root / "tasks/public_examples/repository-debug-100-v1"
    manifest = read_manifest(task)
    if manifest.get("cohort") != "discovery" or manifest.get("family") != "repository-debug":
        raise ValueError("Sample must use the public repository-debug discovery fixture")
    before_hash = task_digest(task)
    environment = {k: v for k, v in os.environ.items()
                   if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    samples = output / "samples"
    samples.mkdir()
    def run(arguments, expected):
        proc = subprocess.run([sys.executable, *map(str, arguments)], env=environment,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != expected:
            raise ValueError("Reference replay command returned an unexpected verifier/runner status")
        return proc.returncode
    with tempfile.TemporaryDirectory(prefix="harborforge-case-study-") as temporary:
        workspace = Path(temporary) / "workspace"
        shutil.copytree(task / "environment/public", workspace)
        original = (workspace / "pipeline.py").read_text(encoding="utf-8")
        run([workspace / "pipeline.py", "--input", workspace / "input/intervals.json", "--output", workspace / "output"], 0)
        before = json.loads((workspace / "output/result.json").read_text(encoding="utf-8"))
        before_status = run([task / "tests/verify.py", "--workspace", workspace], 1)
        run([task / "solution/solve.py", "--workspace", workspace], 0)
        after_status = run([task / "tests/verify.py", "--workspace", workspace], 0)
        after = json.loads((workspace / "output/result.json").read_text(encoding="utf-8"))
        repaired = (workspace / "pipeline.py").read_text(encoding="utf-8")
        _write(samples / "input.json", json.loads((workspace / "input/intervals.json").read_text(encoding="utf-8")))
        (samples / "pipeline.py").write_text(repaired, encoding="utf-8", newline="\n")
    controls = _local_controls(task, manifest, time.monotonic() + 180)
    if not controls or not all(gate["passed"] for gate in controls):
        raise ValueError("Local reference behavioral controls failed")
    if task_digest(task) != before_hash:
        raise ValueError("Public task bytes changed during replay")
    _write(samples / "before.json", before)
    _write(samples / "after.json", after)
    patch = "".join(difflib.unified_diff(original.splitlines(True), repaired.splitlines(True),
                                         fromfile="before/pipeline.py", tofile="after/pipeline.py"))
    (samples / "pipeline.patch").write_text(patch, encoding="utf-8", newline="\n")
    result = {"kind": "local_author_reference_replay", "model_calls": 0, "runtime": "local",
              "container_isolation_verified": False, "eligible_for_model_evaluation": False,
              "task": task.name, "task_sha256": before_hash,
              "before_verifier_exit_code": before_status, "after_verifier_exit_code": after_status,
              "controls": [{"name": gate["name"], "passed": gate["passed"]} for gate in controls],
              "legacy_structure_notice": "This archived public fixture lacks tests/Dockerfile; full current quality/Docker eligibility is not claimed.",
              "explanation": "The broken '<' comparison fails to merge touching half-open intervals; '<=' repairs that contract."}
    _write(samples / "verification.json", result)
    return result


def render(summary: dict, sample: dict, output: Path) -> str:
    esc = lambda v: html.escape(str(v), quote=True)
    rows = "".join(f"<tr><th>{esc(m['model'])}</th><td>{esc(m['date'])}</td><td>{m['passes']}/{m['attempts']}</td>"
                   f"<td><div class=bar><i style='width:{100*m['completion_rate']:.1f}%'></i></div></td>"
                   f"<td>{esc(', '.join(f'{n} {label}' for label,n in m['outcomes'].items()))}</td><td>$0</td></tr>"
                   for m in summary["models"])
    limits = "".join(f"<li>{esc(v)}</li>" for v in summary["limits"])
    provenance = "".join(f"<li><code>{esc(v['file'])}</code><br><small>SHA256 {esc(v['sha256'])}</small></li>"
                         for v in summary["sources"])
    def display_result(path):
        value = json.loads(path.read_text(encoding="utf-8"))
        pairs = ",\n".join("    " + json.dumps(pair) for pair in value["intervals"])
        return '{\n  "intervals": [\n' + pairs + '\n  ],\n  "covered": ' + str(value["covered"]) + "\n}"
    before = display_result(output / "samples/before.json")
    after = display_result(output / "samples/after.json")
    patch = (output / "samples/pipeline.patch").read_text(encoding="utf-8")
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>HarborForge  /  Free-model case study</title><style>
:root{{color-scheme:light;--ink:#173247;--accent:#0b756d;--line:#d5e1e4}}*{{box-sizing:border-box}}
body{{margin:0;background:#f2f6f5;color:var(--ink);font:16px/1.65 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}
h1{{font-size:clamp(2rem,5vw,3.8rem);line-height:1.13;max-width:850px;margin:12px 0 20px}}h2{{margin:0 0 16px;font-size:1.6rem}}
a{{color:var(--accent)}}.eyebrow{{color:var(--accent);font-weight:750;letter-spacing:.12em;text-transform:uppercase;font-size:.8rem}}
.lead{{max-width:800px;font-size:1.15rem}}section{{background:white;border:1px solid var(--line);border-radius:18px;padding:28px;margin:24px 0}}
.metrics,.columns{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.metric{{padding:20px;border:1px solid var(--line);border-radius:14px;background:white}}
.metric strong{{font-size:2rem;display:block}}.columns{{grid-template-columns:1fr 1fr}}.scroll{{overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:.94rem}}th,td{{text-align:left;padding:14px 10px;border-bottom:1px solid var(--line)}}th{{font-weight:650}}
.bar{{width:120px;background:#e5eeee;border-radius:8px;height:12px}}.bar i{{display:block;background:var(--accent);height:100%;border-radius:8px}}
pre{{background:#142d3c;color:#e0f1ef;padding:20px;border-radius:12px;overflow:auto;font:13px/1.55 ui-monospace,monospace}}code{{overflow-wrap:anywhere}}
.note{{border-left:4px solid #c38b36;padding-left:16px}}small{{overflow-wrap:anywhere;color:#526977}}li{{margin:8px 0}}footer{{color:#526977;margin:30px 0}}
@media(max-width:700px){{main{{padding:28px 14px}}section{{padding:20px}}.metrics,.columns{{grid-template-columns:1fr 1fr}}.columns{{grid-template-columns:1fr}}}}
</style><main><header><div class=eyebrow>HarborForge / worked example / November 2026</div>
<h1>Can free models finish scientific Python engineering tasks?</h1><p class=lead>Four models used the same three calibration tasks and two repeats.
This page brings the recorded results together, then walks through one reproducible public repair.</p></header>
<div class=metrics><div class=metric><strong>{summary['trial_count']}</strong>real model trials</div>
<div class=metric><strong>{summary['outcomes'].get('PASS',0)}</strong>verified passes</div>
<div class=metric><strong>{summary['trial_provider_requests']}</strong>trial requests</div><div class=metric><strong>$0</strong>recorded model cost</div></div>
<section><h2>Recorded model results</h2><p>Completion includes every registered attempt. Token/output stops count as unsuccessful completions.</p>
<div class=scroll><table><thead><tr><th>Model</th><th>Run date</th><th>Passes</th><th>Completion</th><th>Outcomes</th><th>Cost</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>Three task templates  /  two repeats  /  fixed terminal-agent harness. {summary['availability_provider_requests']} availability requests are separate;
two earlier protocol-development requests are excluded from this matrix. Rebuilding this report makes zero model calls.</p></section>
<section><h2>The workflow</h2><ol><li>Write an explicit task contract and freeze the input/task version.</li>
<li>Require oracle, alternate and negative controls before model trials.</li><li>Freeze model IDs, harness, repeats and token/time/cost limits.</li>
<li>Run the terminal agent; export its source and output to a separate verifier.</li><li>Retain every outcome and provider receipt, including budget stops.</li>
<li>Compare completion rates and inspect a concrete success or failure.</li></ol></section>
<section><div class=eyebrow>Hands-on public sample  /  reference replay  /  no model call</div><h2>Repair touching intervals</h2>
<p>The input contains duplicate, empty, negative, overlapping and touching half-open intervals. The broken utility keeps touching intervals separate.
Its total length can look right while its interval representation violates the API contract.</p><p class=note>This example executes existing public author/reference code locally. It is not a model response,
a Docker-isolation test, or an additional model score. This archived fixture lacks a separate verifier Dockerfile; only its public behavioral controls are replayed.</p><div class=columns><div><h3>Before  /  verifier rejects</h3><pre>{esc(before)}</pre></div>
<div><h3>After  /  verifier accepts</h3><pre>{esc(after)}</pre></div></div><h3>The source change</h3><pre>{esc(patch)}</pre>
<p><a href=samples/input.json>Input JSON</a>  /  <a href=samples/pipeline.py>Repaired source</a>  /  <a href=samples/pipeline.patch>Patch</a>  /
<a href=samples/verification.json>Verifier/control evidence</a></p><p>Before verifier exit: {sample['before_verifier_exit_code']}; after: {sample['after_verifier_exit_code']}.
Public task hash: <code>{esc(sample['task_sha256'])}</code>.</p></section>
<section><h2>Rebuild without an API key</h2><pre>python scripts/build_free_model_case_study.py --output-dir artifacts/case-study-replay</pre>
<p>Use a new output directory for each replay. Requires the repository's Python environment; no provider SDK, credentials or Docker engine is needed.</p>
<p><a href=summary.json>Structured summary</a>  /  <a href=model-results.csv>Model results CSV</a></p></section>
<section><h2>Evidence and limits</h2><ul>{limits}</ul><h3>Public source receipts</h3><ul>{provenance}</ul>
<p>Matched harness SHA256: <code>{esc(summary['harness_provenance_sha256'])}</code>. Raw calibration tasks, model-generated source and provider traces remain private.</p></section>
<footer>HarborForge Eval Lab  /  Historical calibration, November 2 and 5, 2026  /  Local public reference replay</footer></main></html>"""


def build(root: Path, output: Path) -> Path:
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError("Choose a fresh output directory; case-study evidence is never overwritten")
    summary = read_results(root)
    output.mkdir(parents=True)
    sample = reference_sample(root, output)
    summary["public_reference_sample"] = sample
    _write(output / "summary.json", summary)
    with (output / "model-results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, lineterminator="\n", fieldnames=["model", "model_id", "date", "attempts", "passes", "completion_rate", "cost_usd", "input_tokens", "output_tokens"])
        writer.writeheader()
        writer.writerows({k: model[k] for k in writer.fieldnames} for model in summary["models"])
    (output / "index.html").write_text(render(summary, sample, output), encoding="utf-8", newline="\n")
    return output / "index.html"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build(ROOT, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
