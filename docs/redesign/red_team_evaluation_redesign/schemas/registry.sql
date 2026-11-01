-- DRAFT V1 DESIGN, NOT APPLIED TO THE EXISTING REPOSITORY.
-- SQLite, strict provenance of tasks, harnesses, trials and outcome adjudication.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS task_versions (
    task_version_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    parent_task_version_id TEXT REFERENCES task_versions(task_version_id),
    family TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK(protocol IN ('terminal_agent','qa_completion')),
    cohort TEXT NOT NULL CHECK(cohort IN ('discovery','calibration','holdout')),
    artifact_sha256 TEXT NOT NULL,
    spec_path TEXT NOT NULL,
    mutation_metadata_json TEXT NOT NULL DEFAULT '{}',
    created_utc TEXT NOT NULL,
    UNIQUE(task_id, version),
    UNIQUE(task_version_id, artifact_sha256)
);

CREATE TABLE IF NOT EXISTS validation_runs (
    validation_id TEXT PRIMARY KEY,
    task_version_id TEXT NOT NULL REFERENCES task_versions(task_version_id),
    environment_digest TEXT NOT NULL,
    verifier_sha256 TEXT NOT NULL,
    oracle_pass INTEGER NOT NULL CHECK(oracle_pass IN (0,1)),
    nop_fails INTEGER NOT NULL CHECK(nop_fails IN (0,1)),
    bad_solution_fails INTEGER NOT NULL CHECK(bad_solution_fails IN (0,1)),
    leakage_check_pass INTEGER NOT NULL CHECK(leakage_check_pass IN (0,1)),
    reviewer_status TEXT NOT NULL CHECK(reviewer_status IN ('pending','approved','rejected')),
    valid INTEGER NOT NULL CHECK(valid IN (0,1)),
    evidence_path TEXT NOT NULL,
    created_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_profiles (
    model_profile_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_requested_id TEXT NOT NULL,
    model_actual_id TEXT,
    family TEXT,
    config_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    recorded_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harness_profiles (
    harness_profile_id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL,
    agent_name TEXT,
    agent_version TEXT,
    container_digest TEXT,
    prompt_sha256 TEXT,
    tools_sha256 TEXT,
    config_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    recorded_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    objective TEXT NOT NULL CHECK(objective IN ('adversarial_discovery','fixed_evaluation')),
    cohort TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    frozen_suite_sha256 TEXT,
    preregistration_path TEXT,
    created_utc TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trials (
    trial_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    task_version_id TEXT NOT NULL REFERENCES task_versions(task_version_id),
    validation_id TEXT REFERENCES validation_runs(validation_id),
    model_profile_id TEXT NOT NULL REFERENCES model_profiles(model_profile_id),
    harness_profile_id TEXT NOT NULL REFERENCES harness_profiles(harness_profile_id),
    repeat_index INTEGER NOT NULL CHECK(repeat_index >= 0),
    attempt_number INTEGER NOT NULL DEFAULT 0 CHECK(attempt_number >= 0),
    outcome TEXT NOT NULL CHECK(outcome IN (
      'PASS','MODEL_FAIL','AGENT_TIMEOUT','PROVIDER_ERROR',
      'INFRA_ERROR','INVALID_TASK','UNSCORABLE','RUNNING','QUEUED'
    )),
    reward REAL,
    verifier_status TEXT,
    error_code TEXT,
    trace_artifact_sha256 TEXT,
    final_artifact_sha256 TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    cost_usd REAL,
    wall_time_ms INTEGER,
    started_utc TEXT,
    ended_utc TEXT,
    UNIQUE(campaign_id, task_version_id, model_profile_id, harness_profile_id, repeat_index, attempt_number)
);

CREATE TABLE IF NOT EXISTS trial_events (
    trial_id TEXT NOT NULL REFERENCES trials(trial_id),
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    artifact_sha256 TEXT,
    PRIMARY KEY(trial_id, seq)
);

CREATE TABLE IF NOT EXISTS failure_findings (
    finding_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES trials(trial_id),
    category TEXT NOT NULL,
    evidence_event_seq INTEGER,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewer_notes TEXT,
    model_assisted_hypothesis TEXT,
    created_utc TEXT NOT NULL,
    FOREIGN KEY(trial_id, evidence_event_seq) REFERENCES trial_events(trial_id,seq)
);

CREATE TABLE IF NOT EXISTS artifacts (
    sha256 TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('public','private','sensitive')),
    created_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trials_campaign ON trials(campaign_id, outcome);
CREATE INDEX IF NOT EXISTS idx_trials_task ON trials(task_version_id, model_profile_id);
CREATE INDEX IF NOT EXISTS idx_validation_task ON validation_runs(task_version_id, valid);
CREATE INDEX IF NOT EXISTS idx_findings_category ON failure_findings(category, review_status);
