"""Conservative suggestions; a text signature is evidence of an error, not its root cause."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath


PATTERNS = [
    ("parsing_or_schema", re.compile(r"\b(?:JSONDecodeError|csv\.Error|ValidationError|KeyError)\b")),
    ("missing_dependency_or_api", re.compile(r"\b(?:ModuleNotFoundError|ImportError|AttributeError)\b")),
    ("numerical_or_units", re.compile(r"\b(?:OverflowError|ZeroDivisionError|FloatingPointError)\b")),
    ("behavioral_assertion", re.compile(r"\bAssertionError\b|assertion failed", re.I)),
    ("tool_error", re.compile(r"command not found|No such file or directory|Permission denied", re.I)),
]


def scan_trace(text: str, *, outcome: str, max_findings: int = 30) -> list[dict]:
    """Return line-addressed suggestions; infrastructure errors never become capability labels."""
    if outcome in {"PROVIDER_ERROR", "INFRA_ERROR", "INVALID_TASK", "UNSCORABLE"}:
        return [{"category": outcome.lower(), "source": "trial_status", "review_status": "unreviewed",
                 "confidence": "status_only", "causal_claim": False,
                 "evidence": "Trial classified by evaluator; no capability attribution"}]
    if outcome == "PASS":
        return []
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for category, pattern in PATTERNS:
            if pattern.search(line):
                excerpt = line[:1000]
                signature = re.sub(r"\b\d+\b", "#", excerpt.strip())
                findings.append({"category": category, "line_number": line_number, "evidence": excerpt,
                                 "signature": hashlib.sha256(signature.encode()).hexdigest(),
                                 "source": "deterministic_scanner", "review_status": "unreviewed",
                                 "confidence": "hypothesis", "causal_claim": False})
                break
        if len(findings) >= max_findings:
            break
    if outcome == "AGENT_TIMEOUT":
        findings.append({"category": "budget_exhaustion", "source": "trial_status",
                         "review_status": "unreviewed", "confidence": "status_only", "causal_claim": False,
                         "evidence": "Agent exhausted the predeclared time allowance"})
    if not findings:
        findings.append({"category": "uncertain", "review_status": "unreviewed", "confidence": "unknown",
                         "source": "trial_status", "causal_claim": False,
                         "evidence": "No deterministic error signature; inspect verifier and trajectory"})
    return findings


def analyze_campaign(registry, campaign_id: str) -> list[dict]:
    """Persist suggestions with event evidence; repeated calls do not duplicate findings."""
    prior = registry.list_findings(campaign_id)
    analyzed = {item["trial_id"] for item in prior}
    records = []
    for trial in registry.list_trials(campaign_id):
        if trial["trial_id"] in analyzed or trial["outcome"] == "PASS":
            continue
        suggestions = []
        remaining_bytes = 4 * 1024 * 1024
        remaining_files = 30
        # Read only bounded, hash-verified archived text. Never follow an arbitrary
        # live trace_path into the host filesystem, and keep private artifact refs.
        if trial["outcome"] in {"MODEL_FAIL", "AGENT_TIMEOUT"}:
            from harborforge.adapters.harbor_adapter import redact as redact_trace

            for item in trial.get("trace_files", []):
                if not isinstance(item, dict) or remaining_files <= 0 or remaining_bytes <= 0:
                    break
                relative = str(item.get("path", ""))
                path = PurePosixPath(relative)
                if path.suffix.lower() not in {".txt", ".log", ".json", ".jsonl"}:
                    continue
                length = item.get("byte_length")
                if not isinstance(length, int) or length < 0 or length > min(1024 * 1024, remaining_bytes):
                    continue
                try:
                    content = registry.artifacts.get(item["sha256"])
                    if len(content) != length:
                        raise ValueError("Archived trace length mismatch")
                    text = redact_trace(content.decode("utf-8", errors="replace"))
                    remaining_files -= 1
                    remaining_bytes -= len(content)
                    for suggestion in scan_trace(text, outcome=trial["outcome"], max_findings=10):
                        if suggestion["category"] in {"uncertain", "budget_exhaustion"}:
                            continue
                        suggestions.append({**suggestion, "evidence_artifact_sha256": item["sha256"],
                                            "evidence_path": relative})
                        if len(suggestions) >= 30:
                            break
                except (OSError, ValueError, KeyError) as error:
                    registry.append_event(trial["trial_id"], "trace_evidence_unavailable",
                                          {"path": relative, "sha256": item.get("sha256"),
                                           "error": type(error).__name__})
                if len(suggestions) >= 30:
                    break
        if not suggestions:
            trace = "\n".join(str(event["payload"]) for event in registry.list_events(trial["trial_id"]))
            suggestions = scan_trace(trace, outcome=trial["outcome"])
        for suggestion in suggestions:
            seq = registry.append_event(trial["trial_id"], "failure_evidence", suggestion)
            category = suggestion.pop("category")
            finding_id = registry.record_finding(trial["trial_id"], category, seq, **suggestion)
            records.append({"finding_id": finding_id, "trial_id": trial["trial_id"], "category": category})
    return records
