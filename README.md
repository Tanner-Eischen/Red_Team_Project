# HarborForge Eval Lab

Adversarial benchmark discovery and independent terminal-agent evaluation, built on Python, Harbor, Docker and SQLite. The repository name remains `Red_Team_Project`.

The evaluator grades the agent's exported source and output artifacts in a **separate verifier container**. Oracles and hidden tests are absent from the agent build context. Every scored task must first pass two oracle runs, a no-op rejection, an incorrect-solution rejection, an alternative valid solution and an additional negative mutation.

## Current evidence

The implementation includes three deterministic scientific software families, immutable lineage, controlled tiered campaigns, conservative pre-call budgets, a trial registry, frozen-suite evaluation, trace-backed failure suggestions and static HTML/JSON/CSV reports. The [12 public examples](tasks/public_examples) are synthetic discovery fixtures, never a private holdout.

Wave 3 container/offline validation now uses the actual pinned Harbor 0.13.2 runtime and native Linux Docker. Three fresh f2 tasks passed all 18 reference/negative controls; a semantic ETL child passed seven more controls. Eight malicious boundary probes passed, including protected worker/reward access, detached-process cleanup, artifact attacks and grader failures. The agent has no host bind mounts; the separate trusted verifier runs candidate code under a restricted UID. [Native evidence](docs/evidence/hf01-container-2026-11-01/summary.json) and the [final public-contract addendum](docs/evidence/hf01-contract-addendum-2026-11-01.json) record the exact tested hashes and disclosure-only delta. Those November 1 receipts are container/control results, **zero real model attempts**, not capability scores. The historical [12-task local report](reports/public-pilot/index.html) is preserved unchanged.

[Wave 3 execution and handoff](docs/WAVE3_EXECUTION_2026-11-01.md) separates implemented code, native controls, offline tests and unrun paid gates. Earlier [124-test accounting evidence](docs/HF03_BUDGET_HARDENING_2026-11-01.md) remains a historical snapshot. The owner superseded the initial paid proposal with [an authorized $0 OpenRouter pilot](docs/HF02_FREE_OPENROUTER_2026-11-02.md). Three named free models passed actual availability probes at zero reported cost. The completed frozen calibration matrix recorded 18 attempts and 102 requests at $0: Super VL and Ultra each passed 6/6; North timed out 6/6. These are small development/calibration results, not a general model ranking. The earlier [$15 proposal](docs/HF02_PILOT_BUDGET_PROPOSAL_2026-11-01.md) remains historical and was never spent.

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
redforge task validate --task tasks/generated/scientific-etl-f2-123-v1 --runtime local --evidence-dir artifacts/validation-local
# With a running Linux Docker engine:
redforge task validate --task tasks/generated/scientific-etl-f2-123-v1 --runtime docker --evidence-dir artifacts/validation-docker
```

Implemented families are `scientific-etl` (null quality flags, unit conversion, duplicate resolution), `geospatial-raster` (actual GeoTIFF CRS/grid alignment and NoData), and `repository-debug` (repair an interval-merging API and CLI). Unknown families reject rather than produce placeholder tasks. See [task authoring](docs/TASK_AUTHORING.md).

## Run a controlled experiment

Start with [the zero-spend control configuration](configs/campaigns/public-controls.yaml). It contains no model profiles. To run models, create a new campaign ID and explicitly supply validated model identifiers, dated prices, repeats, matched harness settings, token/time limits and campaign reservations using [the profile example](configs/model_profiles.example.yaml). Provider credentials stay in the host environment. No default model is selected and no scheduled workflow calls paid APIs.

```powershell
redforge campaign run --config configs/campaigns/public-controls.yaml --output-dir artifacts/control-run
redforge campaign analyze --campaign public-container-controls-v1
redforge report build --campaign public-container-controls-v1 --out artifacts/control-report
```

The serial controller respects the concurrency ceiling, preserves retries as distinct attempts, retries provider errors only, and stops on uncertain usage. A separate `max_provider_retries_total` bounds retries across the whole campaign; a total-attempt ceiling alone is not a retry allowance. A campaign ID can be claimed only once, including after a crash before its first final result. Reconciliation requires a new explicitly configured campaign; rerunning cannot silently reset spending limits.

Historical schema-1 discovery retains its failure-only behavior. Explicit [schema-2 discovery](docs/HF04_HF05_OFFLINE_2026-11-01.md) adds bounded all-pass escalation, six evidence-linked semantic operators, confirmed-review authority, matched immutable child reuse and rejection of unchanged cases. These behaviors are tested with explicitly labeled offline adapters; empirical difficulty change remains unmeasured. Manual `task revise` still requires a recorded parent failure via `--from-trial`. Tier 3 requires declared human review. A frozen evaluation bypasses adaptive selection and runs its preregistered matrix:

```powershell
# The config must declare objective=fixed_evaluation, a private cohort,
# tasks, suite_manifest, model profiles, repeats, budgets and timeout policy.
redforge eval freeze --config path/to/private-evaluation.yaml
redforge eval run --config path/to/private-evaluation.yaml
```

The mutation controller rejects calibration/holdout tasks. Frozen manifests bind task hashes and the full experimental configuration, reject drift and cannot be overwritten. Published examples are unsuitable as untouched future holdout.

## What is measured

`PASS` and `MODEL_FAIL` require completed independent verifier evidence and Docker-eligible quality controls. Agent timeout, provider failure, infrastructure failure, invalid task and unscorable attempt are retained separately. Reports preserve all outcomes, exclusions, missing planned attempts, repeated reliability, task-cluster uncertainty and matched parent/child comparisons. The primary completion metric counts timeouts as unsuccessful and uses final-per-repeat outcomes; conditional correctness and missing/error coverage remain separate. `report build --public` rejects private calibration/holdout cohorts before writing. Failure categories remain unreviewed hypotheses until a human adjudication is appended.

Configured-price budgets use conservative input byte/output reservations at each LLM request, including full conversation history and summaries. These can stop an agent earlier than ordinary token accounting. They depend on accurate declared provider prices; they are not a guarantee about provider billing changes. See [methods and limitations](docs/METHODS.md).

## Legacy and roadmap

The original prompt-only LangGraph program is preserved under [legacy](legacy/README.md); its scores are unverified and excluded from the new registry. The root `langgraph_red_team.py` now forwards to the bounded campaign controller. Legacy COBOL remains explicitly unverified. Optional Inspect AI integration, interactive UI and remote scaling follow successful real terminal validation; they are not represented as completed.

The original recommendations and 13-PR backlog are preserved under [docs/redesign](docs/redesign/red_team_evaluation_redesign/README.md).

## Authorized free-provider pilot

The initial broker is OpenRouter. Selected roles are North Mini Code (coding screen),
Nemotron 3.5 Super VL (balanced agent) and Nemotron 3 Ultra (deeper reasoning), all
using exact `:free` SKUs. These role labels are hypotheses, not a model ranking.
Both catalog and endpoint prices must be zero; provider/model fallback, paid
plugins, BYOK routing and response caching are excluded by a host-only guard.
Actual `usage.cost` must equal zero, and token/request/time limits remain active.

The active FCC key is loaded privately into the controller process only. It is
never written into tracked profiles, prompts or task containers. The shared
workspace guardrail now permits the three free variants without deleting prior
model choices. Profile evidence expires and must be renewed before a new frozen
campaign; the guard never substitutes a paid or random model.

`prepare_free_pilot.py` creates a fresh private calibration matrix after price and
account preflight; `run_free_pilot.py` accepts only that zero-price scope. The
scripts refuse reused output directories, preserve historical failures and use
new immutable campaign/harness identities when the protocol changes. The free
matrix has 18 planned trials, no retries and at most 216 guarded inference calls.
Availability probes are separate from benchmark trials. No credit purchase or
paid API route is authorized.
