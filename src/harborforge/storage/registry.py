"""Append-only experiment registry. A recorded result is never silently replaced."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def config_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def redact(value: Any) -> Any:
    """Remove common credentials while preserving usage counts and environment-variable names."""
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if re.search(
            r"(^|_)(api_key|secret|password|authorization|access_token|refresh_token)$", str(key), re.I
        ) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\b(sk-(?:proj-|ant-)?)[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value


class Registry:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.path.parent / "objects")
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            migration = Path(__file__).parent / "migrations" / "001_registry.sql"
            triggers = []
            for table in ("task_versions", "validation_runs", "profiles", "campaigns", "trials",
                          "trial_events", "failure_findings", "finding_reviews", "artifacts"):
                for action in ("UPDATE", "DELETE"):
                    triggers.append(
                        f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} "
                        "BEGIN SELECT RAISE(ABORT, 'Registry rows are immutable'); END;"
                    )
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8")
                                          + "\n" + "\n".join(triggers) + "\nCOMMIT;")
            version = 1
        if version == 1:
            migration = Path(__file__).parent / "migrations" / "002_execution_claims.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            version = 2
        if version != 2:
            self.connection.close()
            raise ValueError(f"Unsupported registry schema version: {version}")

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def register_task(self, manifest: dict, task_path: Path | str) -> str:
        from harborforge.authoring.provenance import task_digest

        path = Path(task_path).resolve()
        if task_digest(path) != manifest["artifact_sha256"]:
            raise ValueError("Task content does not match manifest hash")
        disk_manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if canonical_json(disk_manifest) != canonical_json(manifest):
            raise ValueError("Supplied manifest differs from authored task manifest")
        task_version_id = manifest["task_version_id"]
        prior = self.connection.execute(
            "SELECT manifest_json FROM task_versions WHERE task_version_id = ?", (task_version_id,)
        ).fetchone()
        if prior:
            if prior["manifest_json"] != canonical_json(manifest):
                raise ValueError("Task version ID is already registered with different content")
            return task_version_id
        with self.connection:
            self.connection.execute(
                "INSERT INTO task_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_version_id, manifest["task_id"], manifest["version"],
                 manifest.get("parent_task_version_id"), manifest["family"], manifest["protocol"],
                 manifest["cohort"], manifest["artifact_sha256"], str(path), canonical_json(manifest), utc_now()),
            )
        return task_version_id

    def record_validation(self, task_version_id: str, validation: dict) -> str:
        task = self.get_task(task_version_id)
        supplied_hash = validation.get("task_sha256", validation.get("artifact_sha256"))
        if supplied_hash != task["artifact_sha256"]:
            raise ValueError("Validation evidence must identify the exact registered task hash")
        validation_id = validation.get("validation_id") or f"validation-{uuid.uuid4().hex}"
        with self.connection:
            self.connection.execute(
                "INSERT INTO validation_runs VALUES (?, ?, ?, ?, ?)",
                (validation_id, task_version_id, int(validation.get("valid") is True),
                 canonical_json(redact(validation)), utc_now()),
            )
        return validation_id

    def register_profile(self, kind: str, profile: dict) -> str:
        if kind not in {"model", "harness"}:
            raise ValueError("Profile kind must be model or harness")
        if redact(profile) != profile:
            raise ValueError("Profiles must reference credential environment names, not contain credentials")
        digest = config_digest(profile)
        profile_id = (profile.get(f"{kind}_profile_id") or profile.get("profile_id")
                      or f"{kind}-{digest[:20]}")
        prior = self.connection.execute("SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)).fetchone()
        if prior:
            if prior["kind"] != kind or prior["config_sha256"] != digest:
                raise ValueError("Profile ID is already bound to another immutable configuration")
            return profile_id
        with self.connection:
            self.connection.execute("INSERT INTO profiles VALUES (?, ?, ?, ?, ?)",
                                    (profile_id, kind, digest, canonical_json(profile), utc_now()))
        return profile_id

    def create_campaign(self, config: dict) -> str:
        if redact(config) != config:
            raise ValueError("Campaign configurations must not contain credentials")
        campaign_id = config["campaign_id"]
        objective = config.get("objective", "adversarial_discovery")
        cohort = config.get("cohort", "discovery")
        if cohort == "holdout" and objective != "fixed_evaluation":
            raise ValueError("Holdout is prohibited in adaptive discovery campaigns")
        digest = config_digest(config)
        prior = self.connection.execute("SELECT config_sha256 FROM campaigns WHERE campaign_id = ?",
                                        (campaign_id,)).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("Campaign ID is already bound to a different preregistration")
            return campaign_id
        with self.connection:
            self.connection.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (campaign_id, objective, cohort, config.get("protocol", "terminal_agent"),
                                     digest, canonical_json(config), utc_now()))
        return campaign_id

    def claim_campaign(self, campaign_id: str) -> str:
        """Claim one execution atomically before external work can spend any budget.

        A crash after this claim requires reconciliation under a new campaign;
        absence of a completed trial is never taken as proof of zero spending.
        """
        self.get_campaign(campaign_id)
        claim_id = f"execution-{uuid.uuid4().hex}"
        try:
            with self.connection:
                self.connection.execute("INSERT INTO execution_claims VALUES (?, ?, ?)",
                                        (campaign_id, claim_id, utc_now()))
        except sqlite3.IntegrityError as error:
            raise ValueError("Campaign already claimed for execution; reconcile interrupted work before a new campaign") from error
        return claim_id

    def record_trial(self, trial_dict: dict) -> str:
        trial = dict(trial_dict)
        trial_id = trial.setdefault("trial_id", f"trial-{uuid.uuid4().hex}")
        task = self.get_task(trial["task_version_id"])
        campaign = self.get_campaign(trial["campaign_id"])
        model = self._profile(trial["model_profile_id"], "model")
        harness = self._profile(trial["harness_profile_id"], "harness")
        for other in (campaign, harness):
            if other.get("protocol", "terminal_agent") != task["protocol"]:
                raise ValueError("Task, campaign, and harness protocols must match")
        if task["cohort"] != campaign["cohort"]:
            raise ValueError("Task and campaign cohorts must match")
        if model.get("protocol", task["protocol"]) != task["protocol"]:
            raise ValueError("Model protocol differs from task protocol")
        validation_id = trial.get("validation_id")
        if validation_id is not None:
            validation = self.connection.execute("SELECT * FROM validation_runs WHERE validation_id = ?",
                                                 (validation_id,)).fetchone()
            if not validation or validation["task_version_id"] != task["task_version_id"]:
                raise ValueError("Trial validation does not belong to this task version")
        else:
            validation = None
        if trial["outcome"] in {"PASS", "MODEL_FAIL", "AGENT_TIMEOUT"} and (
            validation is None or not validation["valid"]
        ):
            raise ValueError("Correctness outcomes require valid quality gate evidence")
        if trial["outcome"] in {"PASS", "MODEL_FAIL", "AGENT_TIMEOUT"} and task["protocol"] == "terminal_agent":
            evidence = json.loads(validation["validation_json"])
            if evidence.get("runtime") != "docker" or evidence.get("eligible_for_model_evaluation") is not True:
                raise ValueError("Terminal correctness requires eligible Docker validation, not local controls")
        reward = trial.get("reward")
        if reward is not None and (isinstance(reward, bool) or not isinstance(reward, (int, float))
                                   or not math.isfinite(reward) or not 0 <= reward <= 1):
            raise ValueError("Reward must be a finite number between zero and one")
        if trial["outcome"] == "PASS" and reward != 1:
            raise ValueError("PASS requires full verifier reward")
        if trial["outcome"] == "MODEL_FAIL" and (reward is None or reward == 1):
            raise ValueError("MODEL_FAIL requires a nonpassing verifier reward")
        trial = redact(trial)
        with self.connection:
            self.connection.execute("INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (trial_id, trial["campaign_id"], trial["task_version_id"], validation_id,
                                     trial["model_profile_id"], trial["harness_profile_id"],
                                     trial.get("repeat_index", 0), trial.get("attempt_number", 0),
                                     trial["outcome"], reward, canonical_json(trial), utc_now()))
        return trial_id

    def append_event(self, trial_id: str, event_type: str, payload: dict) -> int:
        with self.connection:
            # BEGIN IMMEDIATE prevents concurrent writers assigning the same sequence number.
            self.connection.execute("BEGIN IMMEDIATE")
            seq = self.connection.execute("SELECT COALESCE(MAX(seq), -1) + 1 FROM trial_events WHERE trial_id = ?",
                                          (trial_id,)).fetchone()[0]
            self.connection.execute("INSERT INTO trial_events VALUES (?, ?, ?, ?, ?)",
                                    (trial_id, seq, event_type, utc_now(), canonical_json(redact(payload))))
        return seq

    def list_events(self, trial_id: str) -> list[dict]:
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in self.connection.execute(
            "SELECT * FROM trial_events WHERE trial_id = ? ORDER BY seq", (trial_id,)
        )]

    def export_events(self, trial_id: str, output_path: Path | str) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(canonical_json(row) + "\n" for row in self.list_events(trial_id)), encoding="utf-8")
        return path

    def get_task(self, task_version_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM task_versions WHERE task_version_id = ?",
                                      (task_version_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown task version: {task_version_id}")
        return {**dict(row), "manifest": json.loads(row["manifest_json"])}

    def get_campaign(self, campaign_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown campaign: {campaign_id}")
        return {**json.loads(row["config_json"]), **dict(row)}

    def _profile(self, profile_id: str, kind: str) -> dict:
        row = self.connection.execute("SELECT config_json FROM profiles WHERE profile_id = ? AND kind = ?",
                                      (profile_id, kind)).fetchone()
        if row is None:
            raise ValueError(f"Unknown {kind} profile: {profile_id}")
        return json.loads(row["config_json"])

    def list_trials(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT r.*, t.family, t.cohort, t.protocol, t.parent_task_version_id, t.artifact_sha256, "
            "t.manifest_json, m.config_json AS model_json, h.config_json AS harness_json "
            "FROM trials r JOIN task_versions t USING (task_version_id) "
            "JOIN profiles m ON r.model_profile_id = m.profile_id "
            "JOIN profiles h ON r.harness_profile_id = h.profile_id "
            "WHERE r.campaign_id = ? ORDER BY r.created_utc, r.trial_id", (campaign_id,)
        )
        return [{**json.loads(row["result_json"]), **dict(row), "model": json.loads(row["model_json"]),
                 "harness": json.loads(row["harness_json"]), "manifest": json.loads(row["manifest_json"])}
                for row in rows]

    def record_finding(self, trial_id: str, category: str, evidence_event_seq: int | None = None,
                       **details: Any) -> str:
        finding_id = f"finding-{uuid.uuid4().hex}"
        details = {**details, "review_status": "unreviewed", "source": details.get("source", "deterministic_scanner")}
        with self.connection:
            self.connection.execute("INSERT INTO failure_findings VALUES (?, ?, ?, ?, ?, ?)",
                                    (finding_id, trial_id, category, evidence_event_seq,
                                     canonical_json(redact(details)), utc_now()))
        return finding_id

    def review_finding(self, finding_id: str, reviewer: str, decision: str, notes: str = "") -> str:
        if not reviewer.strip():
            raise ValueError("Reviewer identity is required")
        review_id = f"review-{uuid.uuid4().hex}"
        with self.connection:
            self.connection.execute("INSERT INTO finding_reviews VALUES (?, ?, ?, ?, ?, ?)",
                                    (review_id, finding_id, reviewer, decision, redact(notes), utc_now()))
        return review_id

    def list_findings(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT f.* FROM failure_findings f JOIN trials t USING (trial_id) WHERE t.campaign_id = ? "
            "ORDER BY f.created_utc, f.finding_id", (campaign_id,)
        )
        findings = []
        for row in rows:
            reviews = [dict(item) for item in self.connection.execute(
                "SELECT * FROM finding_reviews WHERE finding_id = ? ORDER BY created_utc, review_id", (row["finding_id"],)
            )]
            findings.append({**dict(row), **json.loads(row["finding_json"]), "reviews": reviews})
        return findings

    def store_artifact(self, source: Path | str | bytes, *, artifact_type: str = "trace",
                       visibility: str = "private") -> dict:
        item = self.artifacts.put(source) if isinstance(source, bytes) else self.artifacts.put_file(source)
        prior = self.connection.execute("SELECT * FROM artifacts WHERE sha256 = ?", (item["sha256"],)).fetchone()
        if not prior:
            with self.connection:
                self.connection.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                                        (item["sha256"], artifact_type, item["relative_path"], item["byte_length"],
                                         visibility, utc_now()))
        return item
