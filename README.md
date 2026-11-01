# HarborForge Eval Lab

Adversarial benchmark discovery and independent terminal-agent evaluation, built on Python, Harbor, Docker and SQLite. The repository name remains `Red_Team_Project`.

The evaluator grades the agent's exported source and output artifacts in a **separate verifier container**. Oracles and hidden tests are absent from the agent build context. Every scored task must first pass two oracle runs, a no-op rejection, an incorrect-solution rejection, an alternative valid solution and an additional negative mutation.

## Current evidence

The implementation includes three deterministic scientific software families, immutable lineage, controlled tiered campaigns, conservative pre-call budgets, a trial registry, frozen-suite evaluation, trace-backed failure suggestions and static HTML/JSON/CSV reports. The [12 public examples](tasks/public_examples) are synthetic discovery fixtures, never a private holdout.

[Implementation and verification status](docs/IMPLEMENTATION_STATUS.md) distinguishes tested code, local controls and external execution gaps. [Public control report](reports/public-pilot/index.html) contains actual local validation evidence and **zero model attempts**. No model capability scores are claimed. Docker container controls and live model trials remain pending on this workstation because its Docker daemon is unavailable. Local functional controls cannot certify isolation.

## Install and verify

Python 3.12 and uv are required for the locked development environment.

```powershell
uv sync --frozen --extra dev --extra harbor --extra geo
uv run --frozen --extra dev --extra harbor --extra geo ruff check .
uv run --frozen --extra dev --extra harbor --extra geo pytest
uv run --frozen --extra dev --extra harbor --extra geo redforge --help
```

The core wheel also installs with `python -m pip install .`; Harbor and geospatial dependencies are optional extras. [Reproduction instructions](docs/REPRODUCE.md) describe a minimal local control run and the required Docker gates.

## Generate and validate a task

After activating `.venv`:

```powershell
redforge task generate --family scientific-etl --seed 123 --output-dir tasks/generated
redforge task validate --task tasks/generated/scientific-etl-123-v1 --runtime local --evidence-dir artifacts/validation-local
# With a running Linux Docker engine:
redforge task validate --task tasks/generated/scientific-etl-123-v1 --runtime docker --evidence-dir artifacts/validation-docker
```

Implemented families are `scientific-etl` (null quality flags, unit conversion, duplicate resolution), `geospatial-raster` (actual GeoTIFF CRS/grid alignment and NoData), and `repository-debug` (repair an interval-merging API and CLI). Unknown families reject rather than produce placeholder tasks. See [task authoring](docs/TASK_AUTHORING.md).

## Run a controlled experiment

Start with [the zero-spend control configuration](configs/campaigns/public-controls.yaml). It contains no model profiles. To run models, create a new campaign ID and explicitly supply validated model identifiers, dated prices, repeats, matched harness settings, token/time limits and campaign reservations using [the profile example](configs/model_profiles.example.yaml). Provider credentials stay in the host environment. No default model is selected and no scheduled workflow calls paid APIs.

```powershell
redforge campaign run --config configs/campaigns/public-controls.yaml --output-dir artifacts/control-run
redforge campaign analyze --campaign public-container-controls-v1
redforge report build --campaign public-container-controls-v1 --out artifacts/control-report
```

The serial controller respects the concurrency ceiling, preserves retries as distinct attempts, retries provider errors only, and stops on uncertain usage. A campaign ID can be claimed only once, including after a crash before its first final result. Reconciliation requires a new explicitly configured campaign; rerunning cannot silently reset spending limits.

Discovery promotes informative valid failures and can create bounded immutable child tasks, each revalidated and rerun. Manual `task revise` requires a recorded parent failure via `--from-trial`. Tier 3 requires declared human review. A frozen evaluation bypasses adaptive selection and runs its preregistered matrix:

```powershell
# The config must declare objective=fixed_evaluation, a private cohort,
# tasks, suite_manifest, model profiles, repeats, budgets and timeout policy.
redforge eval freeze --config path/to/private-evaluation.yaml
redforge eval run --config path/to/private-evaluation.yaml
```

The mutation controller rejects calibration/holdout tasks. Frozen manifests bind task hashes and the full experimental configuration, reject drift and cannot be overwritten. Published examples are unsuitable as untouched future holdout.

## What is measured

`PASS` and `MODEL_FAIL` require completed independent verifier evidence and Docker-eligible quality controls. Agent timeout, provider failure, infrastructure failure, invalid task and unscorable attempt are retained separately. Reports preserve all outcomes, exclusions, missing planned attempts, repeated reliability, task-cluster uncertainty and matched parent/child comparisons. Failure categories remain unreviewed hypotheses until a human adjudication is appended.

Configured-price budgets use conservative input byte/output reservations at each LLM request, including full conversation history and summaries. These can stop an agent earlier than ordinary token accounting. They depend on accurate declared provider prices; they are not a guarantee about provider billing changes. See [methods and limitations](docs/METHODS.md).

## Legacy and roadmap

The original prompt-only LangGraph program is preserved under [legacy](legacy/README.md); its scores are unverified and excluded from the new registry. The root `langgraph_red_team.py` now forwards to the bounded campaign controller. Legacy COBOL remains explicitly unverified. Optional Inspect AI integration, interactive UI and remote scaling follow successful real terminal validation; they are not represented as completed.

The original recommendations and 13-PR backlog are preserved under [docs/redesign](docs/redesign/red_team_evaluation_redesign/README.md).
