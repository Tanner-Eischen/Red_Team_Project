PRD: HarborForge
================

An application that generates Harbor 2.0 (Terminus) tasks, runs automated validation and multi-model verification, iterates toward targeted failure modes, and records failure patterns for continuous improvement.

1) Summary

----------

HarborForge creates benchmark tasks in the Harbor 2.0 format, validates them (structure, build, oracle, tests, reward artifacts), then runs a configurable set of “verification models” (agents) to attempt the task. It learns from failures by classifying root causes (task quality, harness issues, cheating vectors, model limitations, flaky infra), then mutates or regenerates tasks to increase the probability of desired outcomes (for example: “oracle passes, verifier passes, but model X fails”).

Human-written constraints are out of scope for now. The generator can author instructions and solutions automatically.

2) Goals

--------

1. Generate valid Harbor 2.0 tasks that pass structural and CI-style checks.

2. Automatically build and run tasks end to end:
   
   * Environment build
   
   * Oracle solution run
   
   * Verifier run that produces reward artifacts
   
   * Pytest outputs verification

3. Evaluate tasks against a configurable agent suite (verification models), record success and failure.

4. Iterate automatically to reach target outcomes:
   
   * Produce model failures while keeping oracle and tests correct
   
   * Tune difficulty, ambiguity, and edge cases while staying verifiable

5. Create a failure intelligence layer:
   
   * Categorize failures (instruction mismatch, test gaps, flake, cheating path, environment issue, etc.)
   
   * Store signatures, statistics, and recommended fixes

6. Provide repeatability:
   
   * Deterministic seeds for generation
   
   * Versioned task artifacts
   
   * Reproducible run logs
3) Non-goals

------------

* A full Harbor runner replacement. HarborForge orchestrates Harbor CLI and container runs rather than reimplementing them.

* Guaranteeing tasks are “human-written” or publication-ready.

* Optimizing for speed above correctness in early versions.
4) Target users

---------------

* Task authors who want to scale generation and validation.

* Benchmark maintainers who want a pipeline that produces tasks with controlled pass rates.

* Researchers studying agent failure patterns.
5) Core concepts

----------------

### 5.1 Task artifact (Harbor 2.0)

Generated tasks follow the canonical layout:

* `instruction.md`

* `task.toml`

* `environment/` with Dockerfile or compose

* `solution/solve.sh` (oracle)

* `tests/test.sh` and `tests/test_outputs.py`

### 5.2 Validation stages

HarborForge runs stages in a strict order so failures are actionable:

1. Static spec checks (structure, required files, path rules, config validity)

2. Build checks (environment builds deterministically, pinned deps)

3. Oracle checks (oracle completes successfully, idempotent)

4. Verifier checks (tests produce reward artifact and pass)

5. Agent checks (models attempt task, track outcome, traces, and failure reason)

### 5.3 Failure patterns

Failure patterns are stored as structured “signatures” that include:

* Stage that failed (build, oracle, verifier, agent)

* Error category

* Extracted features (keywords, stack traces, missing files, schema mismatch)

* Minimal reproduction notes

* Suggested mutation or fix strategies
6) User stories

---------------

1. As a user, I can generate N tasks from a template family (debugging, data-processing, etc.) with a target difficulty.

2. As a user, I can run a full pipeline and get a report: pass/fail by stage and by model.

3. As a user, I can specify a target outcome like:
   
   * “Oracle passes, verifier passes, but model suite pass rate under 30%”

4. As a user, I can see common failure patterns across runs and which generator settings correlate with them.

5. As a user, I can reproduce any result from a run ID and regenerate the exact task artifacts.
7) Functional requirements

--------------------------

### 7.1 Task generation

* Generate full task folder with required files.

* Support “task families” (generators) by category:
  
  * debugging, build-and-dependency-management, data-processing, security, etc.

* Provide mutation operators:
  
  * Instruction mutations: add constraints, edge cases, schema requirements, absolute path enforcement
  
  * Environment mutations: add dependencies, adjust pinned versions, introduce realistic misconfigurations
  
  * Test mutations: expand coverage, add edge cases, prevent cheating
  
  * Problem mutations: increase complexity, multi-step dependencies, hidden pitfalls

Inputs:

* Category, difficulty, tags

* Seed

* Family name

* Optional corpus hints (tooling, language, repo style)  
  Outputs:

* Task folder artifact

* Generation metadata file (seed, generator version, mutation history)

### 7.2 Validation and execution

* Static checks:
  
  * Required files present
  
  * Absolute paths referenced for required outputs and inputs
  
  * `task.toml` fields present and valid
  
  * No obvious leakage like embedding solution steps inside tests (heuristic)

* Build:
  
  * Build environment using Harbor or Docker build matching Harbor context

* Oracle:
  
  * Run `solution/solve.sh` in the built environment
  
  * Capture stdout, stderr, exit code, runtime

* Verifier:
  
  * Run `tests/test.sh`
  
  * Assert reward file exists at expected path and format is valid
  
  * Parse CTRF output if produced, otherwise parse pytest output

* Agent run:
  
  * Launch agent in the task environment with a harness that can:
    
    * Provide instruction.md content
    
    * Allow shell commands and file edits
    
    * Enforce timeouts
  
  * Collect:
    
    * Final reward outcome
    
    * Command trace
    
    * File diff summary
    
    * Tokens and cost, if available

### 7.3 Iteration engine

* Support iteration policies:
  
  * Search-based: generate many, filter, keep best candidates
  
  * Mutation-based: start from a seed task, apply targeted mutations
  
  * Curriculum: escalate difficulty until a failure threshold is met

* Target objectives:
  
  * Minimize agent pass rate subject to oracle and verifier passing
  
  * Maximize “interesting failures” based on novelty of failure pattern signatures

* Early stopping:
  
  * Stop when target pass rate and stability criteria met

### 7.4 Failure intelligence and analytics

* Normalize and classify failures:
  
  * Build failure
  
  * Oracle failure
  
  * Verifier failure
  
  * Agent failure
  
  * Flake suspected

* Extract features:
  
  * Error message embeddings or keyword hashing
  
  * Stack trace frames
  
  * Missing path names
  
  * Schema mismatch keys

* Store:
  
  * Run record, task record, model record, failure signature

* Provide dashboards:
  
  * Pass rate by model and category
  
  * Failure categories distribution
  
  * Top recurring signatures
  
  * “Generator setting to failure” correlations

### 7.5 Reproducibility

* All generation uses deterministic seeds.

* All artifacts are versioned:
  
  * Generator version
  
  * Mutation history
  
  * Environment dependency locks

* Each run produces a bundle of logs and metadata.
8) System architecture

----------------------

### 8.1 High-level components

1. **Generator**
   
   * Produces task folder
   
   * Emits generation metadata

2. **Validator**
   
   * Static checks
   
   * Spec conformance checks

3. **Runner**
   
   * Build runner
   
   * Oracle runner
   
   * Verifier runner

4. **AgentRunner**
   
   * Pluggable interface per model provider
   
   * Runs agent attempts and collects traces

5. **IterationController**
   
   * Chooses which tasks to keep, mutate, or discard

6. **FailureAnalyzer**
   
   * Classifies failures and generates signatures

7. **Registry**
   
   * Stores tasks, runs, results, signatures

8. **UI or CLI**
   
   * CLI first, optional web UI later

### 8.2 Suggested tech stack

* Language: Python (fast iteration, strong tooling) or TypeScript if you want parity with your other repos

* Container orchestration: Harbor CLI + Docker

* Storage:
  
  * SQLite for local, Postgres for team scale
  
  * Object store layout on disk for artifacts and logs

* Optional embeddings:
  
  * Local embedding model or provider API, behind a feature flag
9) Data model (minimum viable)

------------------------------

### Task

* task_id

* category, difficulty, tags

* seed

* generator_name, generator_version

* mutation_history (json)

* artifact_path

* created_at

### Run

* run_id

* task_id

* stage_results (json)

* start_time, end_time

* machine_info (cpu, mem, docker version)

* harbor_version

### StageResult

* stage_name (static, build, oracle, verifier, agent)

* status (pass, fail, skip)

* exit_code

* runtime_ms

* logs_path

* error_signature_id (nullable)

### AgentAttempt

* run_id

* model_id

* status (pass, fail, timeout)

* reward_value

* trace_path

* tokens_in, tokens_out, cost (optional)

* failure_signature_id (nullable)

### FailureSignature

* signature_id

* stage_name

* category

* message_hash

* key_features (json)

* example_log_snippet (short)

* first_seen, last_seen

* count
10) Interfaces

--------------

### 10.1 CLI commands (proposal)

* `harborforge gen --family debugging/python --n 10 --seed 123`

* `harborforge validate --task ./out/task_001`

* `harborforge run --task ./out/task_001 --stages all`

* `harborforge eval --task ./out/task_001 --models gpt-*,claude-*`

* `harborforge iterate --family data-processing/csv --budget 200 --objective low_passrate`

* `harborforge report --run <id> --format html`

### 10.2 Pluggable agent runner API

A single interface that can support different providers:

* `run_attempt(task_path, instruction_text, timeouts, tools_config) -> AttemptResult`

AttemptResult includes:

* reward outcome

* trace events (commands, file writes)

* final diff summary

* stdout, stderr pointers
11) Objective functions for iteration

-------------------------------------

Examples:

1. **Hard-but-valid**
   
   * oracle_pass = true
   
   * verifier_pass = true
   
   * agent_pass_rate <= target

2. **Disagreement mining**
   
   * oracle_pass = true
   
   * verifier_pass = true
   
   * model_A fails, model_B passes

3. **Novel failure discovery**
   
   * maximize new failure signatures per compute budget

4. **Stability**
   
   * same agent outcome across K reruns, or low variance
12) Quality and safety constraints

----------------------------------

Even without “human-written” requirements, enforce:

* Determinism when possible (seeded randomness)

* No external network dependency for correctness

* Tests verify behavior, not implementation, to reduce brittle cheating routes

* Reward file must always be produced, even on failure, to avoid harness ambiguity
13) Metrics

-----------

* Task validity rate: percent passing static, build, oracle, verifier

* Agent pass rate by model, category, difficulty

* Flake rate: inconsistent outcomes across reruns

* Novel signature rate: new signatures per 100 runs

* Iteration efficiency: cost per successful “target outcome” task
14) Risks and mitigations

-------------------------

1. **False failures due to harness issues**
   
   * Mitigation: stage isolation, always write reward on failure, rerun suspected flakes once

2. **Generator produces tasks that are trivially solvable**
   
   * Mitigation: add anti-cheat tests, multi-step dependencies, hidden edge cases

3. **Overfitting to a single model**
   
   * Mitigation: multi-model suite, cross-model objectives

4. **Non-deterministic builds**
   
   * Mitigation: pinned dependencies, stable base images, build caching controls
15) Milestones

--------------

### M0: Skeleton and local execution

* Task artifact writer

* Static validator

* Build, oracle, verifier runners

* SQLite registry

### M1: Agent evaluation

* AgentRunner interface

* At least one provider implementation

* Trace capture and scoring integration

### M2: Iteration and learning

* Mutation operators

* FailureAnalyzer with signatures

* IterationController with objectives

### M3: Reporting and dashboards

* HTML report generator

* Aggregated analytics

* Top signatures, pass rates, flake stats

* * *

README Outline (for the repo)
=============================

HarborForge
-----------

Generates Harbor 2.0 tasks, validates them end to end, evaluates against verification models, iterates toward targeted failure outcomes, and learns from failure patterns.

### Install

* Python 3.11+

* Docker

* Harbor CLI available on PATH

### Quickstart

1. Generate tasks  
   `harborforge gen --family debugging/python --n 5 --seed 42`

2. Validate and run pipeline  
   `harborforge run --task out/task_0001 --stages all`

3. Evaluate with models  
   `harborforge eval --task out/task_0001 --models gpt-5,claude-*`

4. Iterate to target failures  
   `harborforge iterate --family debugging/python --budget 100 --objective low_passrate`

### Output structure

* `out/tasks/<task_id>/` task artifacts

* `out/runs/<run_id>/` logs, traces, stage results

* `db/harborforge.sqlite` registry database

### Configuration

* `config.yaml` or `config.toml` for:
  
  * model suite
  
  * timeouts
  
  * compute budget
  
  * objective function
  
  * generator families enabled

### Development

* Add a new generator family

* Add a new model provider

* Add a new mutation operator

* Extend failure categories and signatures

If you want this as a full PRD document with a tighter MVP scope and explicit acceptance criteria per feature, I can rewrite the above into a ticket-ready spec.
