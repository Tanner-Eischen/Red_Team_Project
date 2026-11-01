PRAGMA foreign_keys = ON;
CREATE TABLE task_versions (
    task_version_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    parent_task_version_id TEXT REFERENCES task_versions(task_version_id),
    family TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK (protocol IN ('terminal_agent', 'qa_completion')),
    cohort TEXT NOT NULL CHECK (cohort IN ('discovery', 'calibration', 'holdout')),
    artifact_sha256 TEXT NOT NULL,
    task_path TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    UNIQUE (task_id, version)
);
CREATE TABLE validation_runs (
    validation_id TEXT PRIMARY KEY,
    task_version_id TEXT NOT NULL REFERENCES task_versions(task_version_id),
    valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
    validation_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('model', 'harness')),
    config_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE campaigns (
    campaign_id TEXT PRIMARY KEY,
    objective TEXT NOT NULL CHECK (objective IN ('adversarial_discovery', 'fixed_evaluation')),
    cohort TEXT NOT NULL CHECK (cohort IN ('discovery', 'calibration', 'holdout')),
    protocol TEXT NOT NULL CHECK (protocol IN ('terminal_agent', 'qa_completion')),
    config_sha256 TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE trials (
    trial_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    task_version_id TEXT NOT NULL REFERENCES task_versions(task_version_id),
    validation_id TEXT REFERENCES validation_runs(validation_id),
    model_profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
    harness_profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
    repeat_index INTEGER NOT NULL CHECK (repeat_index >= 0),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 0),
    outcome TEXT NOT NULL CHECK (outcome IN ('PASS', 'MODEL_FAIL', 'AGENT_TIMEOUT', 'PROVIDER_ERROR',
        'INFRA_ERROR', 'INVALID_TASK', 'UNSCORABLE')),
    reward REAL,
    result_json TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    UNIQUE (campaign_id, task_version_id, model_profile_id, harness_profile_id, repeat_index, attempt_number)
);
CREATE TABLE trial_events (
    trial_id TEXT NOT NULL REFERENCES trials(trial_id),
    seq INTEGER NOT NULL CHECK (seq >= 0),
    event_type TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (trial_id, seq)
);
CREATE TABLE failure_findings (
    finding_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES trials(trial_id),
    category TEXT NOT NULL,
    evidence_event_seq INTEGER,
    finding_json TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    FOREIGN KEY (trial_id, evidence_event_seq) REFERENCES trial_events(trial_id, seq)
);
CREATE TABLE finding_reviews (
    review_id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES failure_findings(finding_id),
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'rejected', 'uncertain')),
    notes TEXT NOT NULL,
    created_utc TEXT NOT NULL
);
CREATE TABLE artifacts (
    sha256 TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private', 'sensitive')),
    created_utc TEXT NOT NULL
);
CREATE INDEX trials_campaign ON trials (campaign_id, outcome);
CREATE INDEX validations_task ON validation_runs (task_version_id);
CREATE INDEX findings_trial ON failure_findings (trial_id);
PRAGMA user_version = 1;
