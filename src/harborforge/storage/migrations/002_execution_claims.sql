CREATE TABLE execution_claims (
    campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
    claim_id TEXT NOT NULL UNIQUE,
    created_utc TEXT NOT NULL
);
CREATE TRIGGER immutable_execution_claims_update BEFORE UPDATE ON execution_claims
BEGIN SELECT RAISE(ABORT, 'Registry rows are immutable'); END;
CREATE TRIGGER immutable_execution_claims_delete BEFORE DELETE ON execution_claims
BEGIN SELECT RAISE(ABORT, 'Registry rows are immutable'); END;
PRAGMA user_version = 2;
