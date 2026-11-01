# Evaluation Protocol v0.1 (Proposal)

## Purpose

Measure agent or model capability under documented experimental conditions while separately supporting adaptive adversarial task discovery. This protocol is a design contract. No new model evaluation has been executed for this document.

## 1. Define the measurement target before calling models

Choose one of:

- **Answer generation:** can the model produce a structured/canonical answer from a supplied input without tools?
- **Terminal software engineering:** can the configured model plus agent scaffold modify a repository and meet behavioral tests under specified limits?
- **Scientific software engineering:** can the configured agent correctly handle numerical/units/CRS/NoData/data-provenance constraints in a small environment?

Never combine these scores. Report whether measured subject is a **model-only prompt** or **model + agent scaffold + tools**.

## 2. Task eligibility checklist

- Explicit, self-contained instructions with non-ambiguous behavioral contract.
- Realistic, human-solvable problem. Numerical tolerances are stated when relevant.
- Independent oracle and protected functional verifier.
- Oracle passes in two clean runs. No-op and at least one incorrect solution fail.
- Alternative valid implementation passes where feasible.
- No private oracle/verifier in agent Docker image or accessible filesystem.
- No unauthorized internet dependencies or unpinned runtime resources.
- Deterministic fixtures and reproducible build environment.
- Task family and version traceable by checksum and parent lineage.
- An expert reviewer can explain which capability the task measures.

If any gate fails, the outcome is a **task invalidation**, not a model failure.

## 3. Data partitioning to prevent contamination

**Discovery split:** candidate tasks, including adaptively revised tasks. This split is explicitly optimized and cannot support broad model capability claims.

**Calibration split:** independent known behavior tests for validating scorer/harness changes and prompt setup. The calibration set may be used repeatedly but must not be described as untouched.

**Holdout split:** separately generated and reviewed tasks with no testing during model/prompt/mutation selection. The controller must not have access to the holdout task contents or scores. The final protocol is preregistered (model set, repeats, resources, exclusions, metrics, statistical methods) before release.

**Public vs private:** Publishing the hidden holdout or oracle to the public GitHub repo makes it unsuitable for future uncontaminated measurement, especially for models trained on public repositories.

## 4. Model tiers (selection protocol)

Tier 1: 1–2 inexpensive/local models with correctly supported modalities and tools. Run 1–2 repeats for screening. Early rejection is allowed if problem is trivial or uninformative, but do not claim hard tasks only because inexpensive models failed.

Tier 2: 2 independent model families/providers at middle capability. Run 2–3 repeats, compare full traces and use a human review gate for suspected meaningful failures.

Tier 3: at least 2 independently developed high-capability models, 3–5 repeats per candidate for confirmation. Do not assign tiers by brand alone. Log requested model/version and actual returned snapshot metadata when available.

**All tiers:** same terminal-agent harness, prompts, tools, token/time budgets and verifier. If a model cannot meet tool/API requirements, exclude that comparison and explain why.

## 5. Trial result definition and denominator

Each attempted evaluation must produce exactly one trial status:

| Status | In correctness denominator? | Explanation |
|---|---|---|
| `PASS` | Yes | Verifier confirmed the output contract |
| `MODEL_FAIL` | Yes | Valid task and usable evaluator; final output incorrect |
| `AGENT_TIMEOUT` | Define in protocol | Time budget exhausted. Report separately even if included as non-success |
| `PROVIDER_ERROR` | No | API, auth, rate limits, incompatible request |
| `INFRA_ERROR` | No | Docker, runner, verifier environment failure |
| `INVALID_TASK` | No | Bad oracle/requirements/test or leaked answer |
| `UNSCORABLE` | No | Missing evidence or unavailable verifier |

Report raw status counts and missingness for all tasks and models. Do not hide API failures by dropping them silently. For each pass rate, report numerator, eligible denominator, and all excluded attempts. Predefine whether agent timeouts count as failures for each benchmark; do not change this after inspecting outcomes.

## 6. Core metrics

**Pass@1:** success fraction for one independent attempt per task under fixed conditions.

**Repeated reliability:** when K attempts are collected, report both success-at-least-once (pass@k) and all-attempt success (pass^k) using a clearly stated estimator and model sampling setup. These measure different capabilities. Sample enough attempts for meaningful estimates and state limitations when K is small.

**Coverage:** model performance per task family, skill category, data regime, tool requirement and estimated task complexity. Do not define difficulty solely from frontier-model pass rate.

**Task-failure yield:** count of validated candidates exposing confirmed errors, with total number generated, valid, screened, revised and rejected. The rate is conditional on the search method and model selection.

**Trial efficiency:** median/quantile runtime and token usage, tool invocations, provider cost where measurable, run failures.

**Verifier quality:** positive-control and negative-control performance, false-positive checks from mutation testing, reviewer agreement.

**Failure attribution:** root cause labels with transcript evidence. Separate confidence and unreviewed labels.

## 7. Statistical rules

- Use Wilson intervals for simple independent binary pass rates.
- Repeated attempts on the same task are clustered. Compute task-level metrics or task-cluster bootstrap confidence intervals rather than treating all trials as unrelated.
- Compare model A and B on the same tasks using paired per-task differences and task-cluster bootstrap; include missingness and tool/harness parity.
- Report effect sizes with uncertainty, not arbitrary model fit scores or tiny-study significance claims.
- With small n or severe class imbalance, prominently label conclusions exploratory.
- Repeated task revisions, model checks and candidate selection induce adaptive overfitting. Only frozen holdout can credibly support generalization claims about the studied task distribution.

## 8. Failure analysis protocol

1. Confirm the task's quality gates passed at the exact `task_version_id` and `harness_profile_id`.
2. Confirm the agent actually received the proper task and working tools, not a 401/404 or missing executable.
3. Inspect artifact, verifier assertion, final trace and tool call sequence.
4. Run a deterministic reproduction when possible.
5. Suggest candidate cause categories, but keep AI-assisted interpretations as hypotheses.
6. For high-priority findings, record expert adjudication with evidence and reviewer disagreement.
7. If failure arose from ambiguity/invalid test, invalidate and repair task rather than hardening against it.
8. For valid capability failures, propose a mutation with a predicted mechanism and preserve lineage.

## 9. Reporting rules

Public report must list:

- Clear benchmark scope, task family, dataset/source provenance and novelty limitations.
- Number of generated, invalidated, selected, and frozen tasks.
- Models with exact IDs, dates and harness configuration.
- Number of tasks, attempts and independent trials by split.
- Positive and negative control outcomes and verifier version.
- Pass/fail and infrastructure errors separately.
- Confidence intervals/statistical method, exclusions and data sensitivity.
- Adversarial selection limitations and any model families seen during mutation.
- Examples of legitimate model failures, model successes, invalid-task failures and ambiguous cases.
- Commands or scripts to reproduce the public example run, with sensitive holdout omitted.

## 10. Review checklist for benchmark publication

A reviewer should be able to answer yes to all:

- Could a human solve this from the task specification and provided environment?
- Could a different correct implementation pass the verifier?
- Can the agent solve without seeing its oracle/hidden tests?
- Do negative controls catch shortcuts and incomplete output?
- Can we reproduce the result from task, model, harness, artifact and run IDs?
- Are the measured results actually from model calls, not mocks?
- Are reporting denominators complete and independent holdout claims honest?
