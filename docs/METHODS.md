# Methods and limitations

HarborForge supports two distinct scientific operations: adaptively selecting discovery tasks and evaluating a frozen preregistered suite. Adaptive failure yield is conditional on the search procedure. It is never an unbiased general model failure estimate.

## Scoring boundary

Harbor is pinned to 0.13.2, task schema 1.3. Only `environment/` is the agent Docker build context. It contains public inputs, a buggy source program and public requirements. Oracle programs, controls, hidden cases and verifier code remain outside that context. The agent runs without root or task network access. Harbor exports `/app/pipeline.py` and `/app/output` into a separate verifier environment; it never executes the oracle when grading a model attempt.

The protected verifier checks the actual public output, then executes the submitted source on independent hidden cases, twice for idempotence. On Linux it drops submitted-program privileges to UID/GID 65534, excludes host credentials and restricts run time/output size. Expected answers remain in the protected parent process. This defense requires actual Docker/Linux validation; local checks execute trusted author controls only and do not certify sandbox security.

Oracle twice, alternative correct implementation, no-op, known wrong and additional shortcut/mutation controls must all produce their expected independent scores. A build or verifier infrastructure error invalidates the evaluation, not the model. Runtime isolation additionally checks the built image and records its digest. The base image is pinned by Docker index digest; apt repositories and transitive image build dependencies are not fully snapshot-pinned. Identical builds are not guaranteed until these are frozen; always retain the actual built digest.

## Campaign boundaries

Only Docker-eligible validations can support terminal correctness outcomes. Frozen configurations bind task hashes, models, harness, repeats, budget and timeout denominator policy. A campaign is atomically claimed once before validation or inference. Aborted or interrupted campaigns cannot reset their budgets by reusing their ID. Each provider retry is an additional immutable attempt. Correctness failures are never retried automatically. Execution is serial even when a larger concurrency ceiling is configured.

The supported agent is a thin budgeted Terminus2 subclass; Harbor retains environment execution and verification. The guard currently supports text-only LiteLLM chat. All requests, including summarization, reserve UTF-8 serialized input bytes plus 4096 framing headroom and a capped output allocation. This deliberately conservative token envelope can shorten usable trajectories. Provider retry decorators are bypassed and automatic retries disabled so each retry requires its own reservation. Unknown/lost usage retains the full allocation and stops subsequent calls. Actual usage and reservations are different quantities and are recorded separately.

Dollar controls depend on accurate user-supplied, dated provider prices. Hidden provider-side overhead or price changes cannot be guaranteed by client code. No inference is run with placeholder model profiles or implicit defaults. Model aliases can drift; retain requested IDs and all actual provider/harness metadata available in the trace. Sampling/reasoning differences are explicit model configuration; arbitrary per-model scaffold changes are rejected.

## Results and uncertainty

All attempts enter the registry, including `PROVIDER_ERROR`, `INFRA_ERROR`, `INVALID_TASK` and `UNSCORABLE`. These errors are excluded from correctness denominators and reported separately. Whether `AGENT_TIMEOUT` counts as failure is fixed before a campaign. Partial cohorts and missing planned task/model/repeat cells remain visible. Retried provider errors do not masquerade as new independent correctness attempts.

Reports show raw pass/eligible counts and the mean per-task success fraction. With one eligible attempt per task, Wilson intervals are used. Repeated attempts are clustered by task and use deterministic 2000-sample percentile bootstrap. Paired model comparisons use shared task means under matching protocol, split and harness. One parent/child pair yields a descriptive delta without a fabricated interval. Pass-at-k and all-k-success are empirical fractions over complete preregistered repeated tasks, not an inferred frontier capability claim. Small samples are exploratory. Closely related discovery revisions are correlated; their aggregate intervals do not support independent population claims.

Failure scanning uses archived, hash-verified trace artifacts and line-addressed evidence. A pattern match is a suggestion, not a causal explanation. Findings and human reviews are append-only; disagreements remain observable. No generative correctness judge overrides functional tests.

## Public evidence and outstanding study

The 12 public tasks use synthetic fixtures: four seeds per family. GeoTIFFs are real files with synthetic values, not observations from a named scientific dataset. Public oracle/hidden-case publication makes these examples unsuitable for future uncontaminated holdout claims. A private reserved cohort requires independently chosen scenarios/seeds, expert review and a separate frozen manifest.

The local control pilot records no model trials. The proposed two-model pilot, 144-trial expanded study, empirical difficulty changes, expert scientific review and claims about real model reliability remain unperformed. Optional Inspect AI/QA bridge, interactive UI and remote scaling are deferred according to the plan's terminal-validity gate.
