# Adversarial Benchmark Discovery and Model Evaluation Platform

## 0. Executive decision

Transform the existing `Red_Team_Project` into a **versioned evaluation research platform** capable of (a) discovering difficult, valid benchmark tasks and (b) comparing model capabilities on independent, frozen tasks. Do not build a generic LLM chat wrapper or call an output mismatch a model limitation until the task and environment have independently passed quality controls.

**Proposed working name:** `HarborForge Eval Lab` (keep GitHub repository name unless separately approved).  
**Portfolio objective:** Demonstrate implemented benchmark-generation infrastructure, controlled multi-model evaluation, reliable scoring, repeatable experiments, failure classification, and documented adversarial hardening.  
**Important:** A high failure yield on adaptively selected candidates is *not* an unbiased general model failure rate.

### Outcome levels

1. **Technical validity:** known-good solutions pass and known-bad/no-op solutions fail in isolated environments.
2. **Discovery:** the system generates new tasks, runs models, records failures, proposes structured revisions and revalidates them.
3. **Measurement:** a frozen evaluation suite yields interpretable comparative metrics with full provenance, repeated trials and uncertainty.
4. **Research presentation:** a reproducible case study and report can be rebuilt from the stored run manifests; results are not invented or described as production-scale.

## 1. Code-grounded audit at commit `34ffd34`

| Severity | File(s) | Finding | Required fix |
|---|---|---|---|
| BLOCKER | `harbor_renderer/renderer.py` | Dockerfile has `COPY solution/ /solution/` and `COPY tests/ /tests/`; reference solution and verifier are in the image intended for a model. | Agent image must contain only public problem environment and inputs. Use Harbor runtime solution upload for oracle, with a separate verifier environment for secret checks. |
| BLOCKER | `harbor_renderer/renderer.py` | Generated `test.sh` executes `/solution/solve.sh`; this is a self-referential oracle validation rather than grading agent output. | Read the agent's output artifact and run independent assertions. Test with oracle, no-op, wrong answer and mutation controls. |
| BLOCKER | `harbor_renderer/spec.py`, `renderer.py` | `reward_path` is set to `/reward/output.txt`, confusing a solution artifact with Harbor's `/logs/verifier/reward.{txt,json}`. | Separate `artifact_contract` from `reward_contract`. Pin Harbor version and generate canonical compatible config. |
| BLOCKER | `langgraph_red_team.py` | `iterate_from_failures` is registered but no active graph edge reaches it. | Add a bounded controller with explicit conditional routes and immutable task revisions. |
| HIGH | `langgraph_red_team.py` | `_run_agent_attempt` asks the LLM for a one-line answer and checks exact string equality. This is **not** a terminal-agent trial with edits/tools. | Separate `qa_completion` and `terminal_agent` protocols, each with its own scorer and comparable harness profile. |
| HIGH | `adversarial_task_generator.py` | COBOL packed-decimal is implemented; most other weakness categories fall back to echo-like oracle and dummy inputs. | Replace generic fallback with a registry of fully specified, independently verifiable task families. Fail closed for unimplemented families. |
| HIGH | `harbor_runner.py` | Harbor processes are classified from exit code; `oracle_run` uses `terminus-2` with `oracle/local` instead of a distinct Oracle agent invocation. | Use supported pinned Harbor CLI/API and parse verifier reward plus trial state; distinguish runner errors from scored failure. |
| HIGH | `harbor_runner.py` | `validate_task_verifier` runs local `tests/test.sh` outside a clean agent-attempt context. | Use a purpose-built positive/negative verifier test harness with actual artifacts. |
| HIGH | `langgraph_red_team.py`, `failure_logging.py` | Errors, output mismatches and model behavior are insufficiently distinguished; DB records are predominantly failing attempts, not all attempts. | Log all attempts with outcomes, run/episode IDs, stage errors, traces and reasons; compute correct denominators. |
| HIGH | `.github/workflows/*.yml` | Daily workflow passes `--tasks adversarial_tasks/*` but the CLI expects task IDs; scheduled runs were failing in the GitHub record. | Make offline validation CI reliable. Move paid model runs to manual dispatch; stop workflow auto-committing generated task output. |
| MEDIUM | `harbor_renderer/spec.py` | Custom specification has useful Pydantic contracts but lacks stable hashes, rubric revision, evaluation split, skill tags and lineage. | Preserve Pydantic, add immutable manifest separate from canonical Harbor task.toml. |
| MEDIUM | `.cline/config.json` | Default model choices are older and tie tiers to a few IDs, without immutable model/harness snapshots. | External model registry with provider, version/snapshot, tools, token limits, budget, timeout and price provenance. |
| MEDIUM | `README.md` | Several links use `file:///c:/...`, which are unusable to GitHub viewers; states full model evaluation more strongly than code supports. | Rewrite commands, architecture, verification status and links. |
| MEDIUM | `.gitignore` | Some generated state is ignored, but run metadata/retention strategy is inconsistent. | Use immutable run artifacts, explicit sensitivity classification, .gitignore patterns and public/private dataset publishing rules. |

**Evidence limits:** This was a static review of GitHub source and workflow status. The daily run histories were marked failed, but job logs were unavailable (API returned HTTP 410), so no definitive root cause of those CI failures is asserted. A fresh local clone, Docker build or paid inference test has **not** been performed for this design review.

## 2. Core architecture and boundaries

### Components to retain

- **Harbor:** primary execution and scoring substrate for sandboxed terminal-agent tasks, including Oracle and separate verifier modes. Avoid reimplementing the executor.
- **Python and Pydantic:** task specifications, validators, task-family plugins and experiment controller.
- **LangGraph:** optional orchestration of *generation and critique*, NOT a substitute for the evaluator or an open-ended self-referential retry loop.
- **SQLite:** run registry with indices, migrations and append-only semantics for trials and observations; JSONL for portable event streams.
- **Docker:** sandbox and pinned build environments. Use rootless/user isolation and no network where compatible.

### New custom evaluation infrastructure

1. **TaskFactory:** deterministic generators and controlled LLM-proposed tasks, with executable reference solutions and test contracts.
2. **TaskQualityGate:** build reproducibility, spec validity, oracle positive, no-op negative, wrong-solution negative, hidden verifier isolation, flaky-test checks and artifact checks.
3. **CampaignController:** tiered multi-model routing with cost/token/time budgets, attempts, rate limiting, retry/cancellation, and clear final trial status.
4. **AdapterRegistry:** Harbor terminal-agent adapter and Inspect/direct QA adapters with consistent event schema, not identical scoring rules.
5. **ExperimentRegistry:** versioned tasks, immutable content hashes, model/harness snapshots, trials, transcript paths and artifact hashes.
6. **FailureIntelligence:** deterministic error extraction, trace indexing, potential failure taxonomy, optional model-assisted explanation and human adjudication.
7. **MutationController:** changes a task family by controlled semantic/edge-case mutations, validates each child and compares it to its parent under the same evaluation protocol.
8. **HoldoutEvaluator:** a separate operation that only accepts frozen task versions and predefined model configs. It cannot mutate or inspect reserved evaluation tasks during selection.
9. **Reporter:** static HTML/CSV/JSON analytical report and optional local dashboard. No score without denominator and quality state.

### Two distinct evaluation protocols

**Protocol A: `qa_completion`.** Prompt-model tests with structured/exact/numerical grading and no tools. Allows inexpensive screening for isolated reasoning/format behavior. Report explicitly as *answer generation*, not coding-agent ability.

**Protocol B: `terminal_agent`.** Harness supplies an initial repository/files, tools and task instructions. Agent edits files and invokes commands. Hidden tests run in a clean verifier environment. Grading is the verifier reward. Models compared under the same tool allowance, system prompt strategy, token budget, wall time and harness.

The same underlying task theme may have both protocols, but their scores must be segregated. The platform should never silently combine them.

## 3. Task creation and quality assurance

### Task family registry

Start with **three implemented task families**, rather than dozens of generic weakness tags:

- `python-data-validation`: broken ETL transforming time-series readings with duplicate timestamps, missing values, strict output schema, unit conversion and idempotence.
- `geospatial-raster`: small, real-data-shaped raster ingestion with CRS/affine/NoData alignment, reprojection, zonal statistics and correct georeferenced exports; use small prepacked fixtures, not internet fetches.
- `software-debugging`: a miniature Python or TypeScript repository with a real bug, failing behavioral tests and reasonable implementation flexibility.

Retain COBOL COMP-3 as a **legacy demonstration** and migrate only after tests validate it. Reject other categories until they have a legitimate family implementation.

### Generation strategies

- Deterministic family templates with seeded fixture generation.
- LLM proposes variants from a structured *public specification*; no access to held-out cases or model-private scores.
- Mutations operate on identifiable dimensions: constraint interaction, input distributions, dependency boundaries, data quality, statefulness and multi-step planning, with the intended difficulty mechanism documented.
- A child task gets a new version and artifact hash; never edit a task after evaluation in place.
- Generate a reference/oracle independent of the verifier implementation where practical. The verifier must check behavior, not duplicate a literal patch.

### Minimum acceptance gates before any model trial

1. Specification is unambiguous, with explicit inputs, outputs, units, allowed resources and success behavior.
2. Build is pinned and repeatable, dependencies available without runtime network.
3. Oracle solution produces expected artifacts and full reward on two clean runs.
4. No-op agent and deliberately incorrect solutions fail with the expected reward.
5. At least one behavior-preserving alternative solution passes (where feasible), proving tests are not too implementation-specific.
6. Negative mutations induce expected test failures, including edge cases and anti-shortcut checks.
7. Hidden tests, secrets and reference solution are absent from the agent-visible container layers/workspace.
8. Artifacts, stdout/stderr, tests and evaluator status are traceable to content hashes.
9. Human or documented expert review signs off on ambiguity, plausibility and novelty before promotion to a public showcase.

If these fail, mark `INVALID_TASK` or `VALIDATION_ERROR`; do **not** score a model failure.

## 4. Model tiers and cost-controlled evaluation

Use configurable provider/model IDs instead of hardcoding one model list. Model names and prices are volatile; the example YAML contains placeholder IDs and must be edited to actually run.

| Stage | Purpose | Proposed protocol | Promotion rule |
|---|---|---|---|
| Preflight | No external models | Oracle, no-op, bad-solution tests | All validation gates pass |
| Tier 1 | Cheap discovery | 1–2 small/local or inexpensive models, 1–2 repeats | Candidate has a meaningful and reproducible behavior gap |
| Tier 2 | Mid capability check | 2 dissimilar providers/families, matched harness, 2–3 repeats | Failure likely represents task difficulty, not harness or config |
| Tier 3 | High capability confirmation | 2+ independent frontier models, 3–5 repeats | Stable failure evidence, independent human review of contentious cases |
| Holdout | Independent measurement | Frozen suite, frozen configs, predetermined repeats | Publish complete cohort with uncertainty, not only failed examples |

Promote candidates using novelty, validity and information value, not just the raw failure fraction. Do not spend on Tier 3 when a task fails basic validation or a cheap agent is blocked by missing tooling. Set per-campaign **dollar ceiling, token ceiling, wall-time ceiling, concurrency ceiling, per-model retries and stop conditions**. Repeated API errors must create `INFRA_ERROR` or `PROVIDER_ERROR`, not model failures.

**Fair comparison:** record model ID and dated snapshot where available, provider, temperature/reasoning controls, system prompt, max tokens, tools, agent framework version, time limit, network access and runtime image digest. Match the available conditions as closely as the provider supports; disclose deviations. Comparing a weak prompt-only model with a strong terminal agent is a harness comparison, not a pure model comparison.

## 5. Closed-loop adversarial revision

The orchestration controller owns the bounded loop. LangGraph can propose or critique, but cannot silently mutate a benchmark's evaluation set.

1. `GENERATED`: produce task version and generation metadata.
2. `VALIDATING`: run quality gates; reject invalid candidates.
3. `SCREENING_T1`: evaluate cheap tier and collect complete traces.
4. `ANALYZING`: classify failures, remove infra/test-invalid examples, isolate evidence.
5. `REVISION_PROPOSED`: choose one controlled mutation and predicted effect.
6. `REVALIDATING`: rebuild, rerun oracle, no-op and faulty controls; compare child to parent under same harness.
7. `SCREENING_T2` and `CONFIRMING_T3`: only if gates and budget allow.
8. `APPROVED_DISCOVERY`: add valid, frozen example to discovery results. It does **not** become a held-out measurement simply because it is hard.
9. `FROZEN_HOLDOUT`: independently authored/reserved task versions evaluated exactly once per pre-registered campaign protocol, without tuning to those results.

Enforce `max_iterations_per_lineage`, `max_model_attempts`, `max_budget`, `no_revision_if_task_invalid`, `human_review_required_for_publish`, and `holdout_never_used_for_selection`.

### A valid hardening iteration

- Observation: strong models read a CSV but treat a missing flag value as zero; this leads to wrong summary statistics.
- Mechanistic hypothesis: ambiguity/edge handling in a multi-stage transform.
- Parent task: define correct semantics and run the agent.
- Child mutation: add a realistic data source whose flag field has blanks and a second pipeline stage that must preserve null semantics, while clearly documenting the contract.
- Verification: independent oracle, no-op negative, known-bad negative, alternate valid solution.
- Evaluation: same model/harness and attempts on parent/child; analyze output and tool traces. Report actual differences only after rerunning.

**Invalid hardening:** adding invisible requirements, inconsistent instructions, flaky dependencies or tests that accept only one implementation. These produce artificial failures rather than useful evals.

## 6. Failure intelligence and trace analysis

Every attempt is logged, not just failures. Treat `outcome` and `failure_cause` as independent dimensions:

- `PASS`: artifact meets contract.
- `MODEL_FAIL`: valid task and evaluator; solved incorrectly or not at all within configured limits.
- `AGENT_TIMEOUT`: valid attempt, model/agent exhausted the predeclared time budget; label separately for analysis.
- `PROVIDER_ERROR`: rate limit, unavailable model, API/auth/capability mismatch.
- `INFRA_ERROR`: container/tool/environment failure not attributable to the model.
- `INVALID_TASK`: inconsistent rubric, broken oracle, leaked answer, impossible task.
- `UNSCORABLE`: missing/inconsistent evidence or evaluator failed.

Failure taxonomy is a secondary label: misunderstanding of specification, wrong algorithm, tool misuse, incomplete dependency chain, numerical/units error, parsing/data schema error, hallucinated API, unsafe shortcut/reward hack, excessive context or budget exhaustion, refusal, and uncertain/other.

Build a transcript scanner that extracts command/tool events, errors, edits, test outcomes, final artifacts, execution times and candidate explanations. Automated labels are suggestions until verified by deterministic evidence or expert review. Store source trajectory excerpts and links, allow annotator disagreement, and support corrections. Failure grouping should use stable signatures plus contextual embeddings optionally; never assume all matching error strings reflect one cause.

## 7. Reproducible model evaluation protocol

**A. Discovery pool:** tasks may be mutated adaptively after seeing model outputs. Report search history, number of generated/invalid/discarded tasks, confirmation repeats and cost. A selected candidate's failure rate is conditional on discovery.

**B. Fixed calibration suite:** known easy/medium/hard tasks with independent expected results. Check that new harness versions and prompt configurations do not change established scoring behavior. If they do, restart comparisons under a versioned configuration.

**C. Frozen held-out suite:** independently developed or reserved task families/seeds. Once frozen, do not expose holdout outcomes to the mutation controller. A new model or harness config requires a fresh preregistered evaluation entry; data shown to generator must never be reused as untouched holdout.

**Metrics by family and model** (all with denominators and missingness):

- Per-task binary pass/fail at a prespecified number of independent attempts; pass@1, optional pass@k and consistency/pass^k style metrics (define estimators and attempt sampling explicitly).
- Failure categories and infrastructure error rates, separately.
- Task validity rate and anti-shortcut / negative-control catch rate.
- Token usage, cost (when disclosed), time to solution, number of tool calls, and incomplete attempts.
- Parent vs child failure probability under paired tasks and unchanged harness, with uncertainty.
- Cross-family transfer: did a mutation that tripped one model also trip held-out models, not just the target?

Use Wilson intervals for simple binary rates at the appropriate independent unit. For repeated attempts per task, use task-cluster bootstrap or a task-level estimator instead of treating all trials as independent. For paired model comparisons, use paired task-level differences with clustered intervals. Predeclare task grouping, model/repeat counts and exclusions. Do not claim significance from a tiny pilot.

**Judges:** deterministic functional/contract tests are primary. LLM-as-judge is suitable for explanation assistance or genuinely open-ended dimensions only after rubric calibration against human-adjudicated examples and with disagreement tracked. It must not quietly override deterministic functional tests.

## 8. Portfolio-quality showcase deliverables

### Case study: scientific Python benchmark suite

Build a **12-task, three-family pilot** (suggestion, not existing result): four scientific ETL tasks, four geospatial/raster tasks and four small repository repair tasks, plus representative easy control cases. Freeze tasks and publish their validity evidence. Screen with one low-cost and one mid-tier model, then compare two or more dissimilar capable models using the same terminal harness. Three attempts per model/task yields 144 agent trials for 12 tasks × 4 models × 3 attempts, before screening and validation. Actual budget depends on model IDs, token consumption, container runtime and provider prices.

**Demonstration assets:** task family README, sample task artifact, independent oracle/no-op/wrong-answer gate reports, model × task heatmap, pass rate with uncertainty by model and family, one failure trajectory with diff and verifier evidence, parent-child task lineage, cost/log provenance, and concise methods/limitations page.

Start with a **smaller pilot** of 2–3 tasks and two models to avoid runaway spend. Report final numbers only after genuine runs. Do not represent CI checks, mocked agent tests, or synthetic demonstration results as measured model performance.

### Suggested README/resume phrasing, once implemented and verified

> Developed an adversarial model-evaluation pipeline that generates versioned coding tasks, validates independent oracles and hidden verifiers, executes reproducible multi-model agent trials, and analyzes failure trajectories. Implemented experiment tracking, controlled task revision and statistical evaluation reports.

Add numeric achievements only when the underlying artifact and denominator exist.

## 9. Priority PR sequence and stop conditions

| PR | Priority | Deliverable | Exit gate |
|---|---|---|---|
| 01 | P0 | Package and baseline audit; freeze legacy results as unverified; offline CI | Clean install, lint, unit/smoke tests, no paid APIs required |
| 02 | P0 | Repair renderer/Harbor task compatibility and oracle/test separation | Oracle=pass, no-op=fail, wrong solution=fail, hidden grader inaccessible |
| 03 | P0 | Canonical terminal-agent runner/adapter with reward parsing and resource limits | One real agent trial captured with correct score vs infra status |
| 04 | P0 | Registry, immutable task revisions, run/trial/event/artifact schemas | Distinct complete attempts recoverable by IDs and content digests |
| 05 | P1 | Task-family plugins and robust generator, with independent test contracts | Three families build and pass all quality gates |
| 06 | P1 | Model tier profiles and budget-aware campaign controller | Stop/promotion logic behaves deterministically; no silent overspend |
| 07 | P1 | Failure scans and human adjudication interface/data | Reproducible annotated failure with trace evidence and correction workflow |
| 08 | P1 | Bounded mutation/iteration and parent-child comparison | Actual validated child is screened and compared; holdout never read |
| 09 | P1 | Frozen evaluation protocol, repeats, uncertainty, report bundle | Complete run matrix, denominators, exclusions and config snapshots |
| 10 | P1 | Scientific case study and static HTML dashboard | A reviewer can reproduce at least one real eval and inspect evidence |
| 11 | P2 | Inspect AI bridge, extra modalities, scaling/postgres | Only after terminal pipeline is trustworthy |

**Minimum viable credible release:** PRs 01–04, one fully validated family from PR 05, controlled Tier 1/Tier 2 evaluation from PR 06, basic failure analysis and a fixed report. Do not delay this release for a full interactive dashboard.

## 10. What to avoid

- Reporting model failures when API authentication or Docker execution failed.
- Scoring the oracle's output as if it were agent-produced.
- Letting agents read or infer hidden verifier code or test solutions.
- Iterating on the same holdout repeatedly and calling the best discovered failures an independent benchmark.
- Changing generation temperature, tools, max tokens, or agent framework across compared models without clear disclosure.
- Using a generative judge as the sole correctness authority for an executable coding task.
- Optimizing for pass rate alone by creating underspecified or impossible tasks.
- Scheduling API-funded CI runs or auto-pushing private benchmark artifacts before basic safeguards pass.

## 11. Open implementation decisions

1. **Task scope:** initially prioritize coding/ETL/geospatial agentic tasks, retain COBOL for regression. Expand science/physics later with specialized oracles.
2. **Execution mode:** Harbor as primary terminal-agent executor, Inspect optional later; do not run the same candidate through multiple mismatched harnesses and combine scores.
3. **Storage:** SQLite + filesystem artifacts for solo development; Postgres/object storage only if concurrency or collaboration demands it.
4. **User interface:** static export first, lightweight Streamlit report viewer second, React/TypeScript UI only after evaluation trace schema is stable.
5. **Benchmarks:** maintain a small publicly inspectable example set and a private independent holdout. Publishing private tests publicly destroys their future holdout status.

## 12. External standards/references

- Harbor official task contracts and verifier isolation: https://github.com/harbor-framework/harbor/blob/main/docs/content/docs/tasks/index.mdx
- Harbor task authoring instructions: https://github.com/harbor-framework/harbor/blob/main/skills/create-task/SKILL.md
- Inspect AI task/scoring/sets/scanners: https://inspect.aisi.org.uk/ ; https://inspect.aisi.org.uk/scoring.html ; https://inspect.aisi.org.uk/eval-sets.html ; https://inspect.aisi.org.uk/scanners.html
- AISI evaluation playbook: https://engineering-playbook.aisi.org.uk/evaluate.html
- Coding benchmark integrity and contamination: https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ ; https://openai.com/index/separating-signal-from-noise-coding-evaluations/
- NIST AI RMF Generative AI Profile: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
