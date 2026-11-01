# Reproduce

Use Python 3.12 and uv 0.11.28. No browser is required or launched by these commands.

```powershell
uv sync --frozen --extra dev --extra harbor --extra geo
.\.venv\Scripts\Activate.ps1
ruff check .
pytest
python scripts/check_public_tasks.py
```

Generate a fresh ETL task and prove functional controls locally:

```powershell
redforge task generate --family scientific-etl --seed 123 --output-dir tasks/generated
redforge task validate --task tasks/generated/scientific-etl-123-v1 --runtime local --evidence-dir artifacts/local-123
```

The evidence must show both oracle runs and the alternate passing; no-op, wrong and negative mutation must be rejected. `eligible_for_model_evaluation` remains false for local checks. Generated packages are immutable: choose a new seed or output directory instead of overwriting. Evidence must stay outside the package or its hash becomes invalid. Git attributes preserve exact authored bytes for cross-platform hashes, including the CSV writer's CRLF records.

Rebuild the full public control report without any provider calls:

```powershell
python scripts/build_public_pilot.py --validate --evidence-dir artifacts/public-replay --report-dir artifacts/public-report
```

This reuses checksum-verified public fixtures. Reports are files only; inspect them with authorized tooling. HTML structural/content checks run without launching a browser. The checked-in report deliberately has no model scores.

A running Linux Docker engine is required for real isolation controls:

```powershell
docker info
redforge task validate --task tasks/generated/scientific-etl-123-v1 --runtime docker --evidence-dir artifacts/docker-123
```

Docker gate failures return a nonzero CLI status. A stopped engine gives `VALIDATION_ERROR`, never a scored model failure. The manual `Container control validation` workflow can execute these controls on a Docker-capable GitHub runner. It uses no model secrets and performs no auto-commit/push. That remote workflow has not been dispatched as part of local verification.

For a real experiment, supply an explicit campaign configuration as described in README, ensure all required provider credentials are available in the host environment, and verify the current model IDs and prices yourself. Use a new campaign/profile ID for changed settings. `redforge campaign run` requires Docker controls; it cannot use local validation as a bypass. `redforge eval freeze/run` requires an exact preregistered frozen configuration and refuses task/configuration drift.

Reports can be rebuilt from an existing registry:

```powershell
redforge campaign analyze --campaign YOUR_CAMPAIGN --registry artifacts/registry.sqlite
redforge report build --campaign YOUR_CAMPAIGN --registry artifacts/registry.sqlite --out artifacts/report
```

`summary.json`, `trials.csv`, `trials.jsonl`, `tasks.jsonl` and per-trial event JSONL retain configuration, outcome counts, missingness and evidence. Content-addressed objects live beside the registry. Do not delete them if replay or failure adjudication is still required. Retention is manual; private traces and holdout fixtures are ignored by Git and must be reviewed before any publication.
