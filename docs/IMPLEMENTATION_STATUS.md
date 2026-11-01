# Implementation scope and verification

The source proposal is preserved in [redesign/red_team_evaluation_redesign](redesign/red_team_evaluation_redesign/README.md).
The separately supplied Downloads/IMPLEMENTATION_PLAN.md contains the same recommendations.

## Plan before implementation

1. Repair `harbor_renderer`, `harbor_runner`, and new validation/adapter modules: agent image has public files only; separate Harbor verifier scores exported artifacts; oracle, no-op, wrong, and alternative controls are independent runs.
2. Add installable `src/harborforge` package and offline CI; remove scheduled paid inference and automated commits.
3. Implement seeded scientific ETL, real small geospatial raster, and repository repair families with immutable manifests and bounded child revisions.
4. Add SQLite registry, hashed artifacts, events, reviewed findings, frozen suites, and static reports.
5. Integrate validated configuration, model tiers, conservative budgets, bounded retries, and discovery versus fixed evaluation commands.
6. Verify installation, lint, behavioral/contract/integration tests, generated controls, CLI flow, and Docker when available. Record external runtime gaps separately from test results.

Dependencies: Python 3.12+, Pydantic, PyYAML; pinned Harbor 0.13.2 for terminal execution; rasterio/NumPy for GeoTIFF family. Existing LangGraph tooling remains optional, explicitly legacy QA rather than terminal evaluation.

Risks: model/provider alias drift, unknown cost/usage, task or scorer leaks, malformed Harbor results, task revisions contaminating holdout, and treating task or infrastructure errors as model failures. Controls fail closed; local validation never claims container isolation or real model performance. Real paid campaigns require explicit user-supplied model profiles, cost caps, and budgets.

## Execution evidence

Existing historical results are unverified legacy data and excluded from new experiment metrics.

| Workstream | Implementation | Observed verification / remaining gate |
| --- | --- | --- |
| Installable package and CI | Complete; locked dependencies, CLI, offline CI, manual Docker controls; paid schedules/auto-push removed | Editable install and independent core-wheel install exercised; no remote workflow dispatched |
| Oracle/verifier isolation | Separate Harbor schema-1.3 verifier and declared source/output transfer; protected expected cases | Local positive/negative controls pass; actual Docker isolation remains unverified |
| Terminal-agent adapter | Harbor 0.13.2, per-trial reward parsing, stage-specific errors, bounded Terminus2 | Contract tests and recording fake-provider request checks pass; no real model trial |
| Task families | ETL, real GeoTIFF CRS/grid/NoData, repository API repair; immutable manifests | 12 public seeds locally validated; all oracle/alternate/no-op/wrong/shortcut controls behave correctly |
| Registry and provenance | SQLite schema migrations, immutable rows, artifacts, event JSONL and one-time atomic campaign claims | Persistence, drift, restart, concurrency and hash-integrity regressions pass |
| Tier controller | Matched profiles, explicit budgets/repeats, conservative reservations, error-only retries and review gate | Deterministic adapter contract tests pass; no paid campaign |
| Failure intelligence | Hash-verified archived evidence, line references, noncausal suggestions and append-only human reviews | Scanner, evidence and adjudication tests pass; no invented real failure case study |
| Mutation | Failure-driven child generation, full revalidation, bounded lineage and later-tier retesting | Parent/child controller tests pass; empirical model-difficulty change has not been measured |
| Frozen evaluation | Exact preregistration/task hashes, immutable suite, holdout rejection in discovery | Drift/configuration/complete-matrix tests pass; no independent held-out measurement performed |
| Static report | HTML/CSV/JSON, outcomes, exclusions, missingness, cluster statistics, task/model matrix and lineage | Public report contains actual local controls and zero model attempts; no browser/UI was accessed |
| Optional Inspect/UI/scaling | Deferred | The supplied plan gates these on a trustworthy, live-validated terminal pipeline |

The separate downloaded plan's SHA-256 matches the extracted plan:
`cc2ad1d6b77885ab85331c35b6906faf94a9e901e48cd4aea5b59f5a3d3a2b27`.

Docker engine check and an actual Docker quality invocation failed because the Linux engine pipe is absent. Attempting to start the existing Docker service through PowerShell failed due to service access. No browser, Docker UI, platform UI, deployment, manual remote workflow dispatch or paid inference was used during implementation verification.

The pinned Docker base index digest was verified through Docker's registry CLI. Apt repositories and transitive image-build dependencies remain mutable; a repeatable bit-for-bit container build is not claimed. Local controls do not certify privilege or network isolation. Expert task/publication review is still pending.

Final pre-commit verification: locked-environment full suite **97 passed**. Ruff and staged Git diff whitespace checks passed, with Git attributes recognizing intentional CSV CRLF records, Markdown hard breaks and archived legacy whitespace. All **245 public example files** match their staged bytes, and all 12 manifest hashes verify. The built core wheel installed in a separate environment, generated a task, initialized SQLite schema 2 and passed local controls. The public report's HTML/JSON/CSV and zero-attempt state were checked without UI access.

The raster stack emits pending Affine deprecation warnings; these do not fail the functional checks. `uv.lock` pins the tested dependency resolution.
