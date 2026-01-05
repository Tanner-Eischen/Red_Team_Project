# Adversarial AI Red-Teaming System

Complete system for generating adversarial problems, testing AI models, and documenting failure patterns.

The goal of this README is to make it easy to get from “fresh clone” to “successful red‑team run” with as few surprises as possible.

## What This Does

1. Generate standardized adversarial tasks targeting specific weakness categories
2. Validate those tasks against multiple AI models (GPT‑4o, Claude, Llama, local models via Ollama)
3. Analyze failure patterns and success rates
4. Persist results and reports for team learning

## Prerequisites

- Python 3.11
- Git
- Bash shell available in your environment  
  - On Windows, use WSL2 or Git Bash for running the `tests/test.sh` scripts.
- Optional but strongly recommended:
  - Docker (for full Harbor/Docker validation pipeline)
  - Harbor 2.x CLI (`harbor`) on your `PATH` (or set `HARBOR_CLI` env var)
  - Ollama running locally with `starcoder2:instruct` pulled (if you want StarCoder‑driven task variants)

### API keys

Set the following environment variables (for whichever providers you intend to use):

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY` (if you configure Anthropic models in `.cline/config.json`)

These are read indirectly via the `.cline/config.json` `solver_models` configuration.

## Installation

From a fresh clone:

```bash
git clone <this-repo-url>
cd Red_Team_Project

python -m venv .venv
source .venv/bin/activate    # Windows (WSL/Git Bash): source .venv/Scripts/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt  # optional: linters/type checking
```

If you use Ollama, ensure it is running and has the StarCoder model:

```bash
ollama pull starcoder2:instruct
```

## Configuration

Most behavior is configured via [`.cline/config.json`](file:///c:/Users/tanne/Stellar/Red_Team_Project/.cline/config.json):

- `adversarial_mode.problem_generator`  
  - Controls how adversarial prompts are generated (Ollama host/model, temperature, etc.).
- `adversarial_mode.solver_models`  
  - List of models used in the red‑team pipeline, including API key env var names.
- `cline.task_directory`, `cline.context_directory`, `cline.output_directory`  
  - Base directories used by generators, logs, and results.

You can start by leaving this file as‑is and only adjusting:

- `host` if your Ollama server is not at `http://localhost:11434`
- `solver_models` entries if you want to swap in different models

## Quick Start: One End‑to‑End Run

This is the shortest “happy path” from zero to a successful adversarial run.

```bash
# 1. Generate a single COBOL packed-decimal adversarial task
python adversarial_task_generator.py --weakness COBOL_PACKED_DECIMAL --num-tasks 1

# 2. Move into the generated task directory
cd adversarial_tasks/cobol_packed_decimal-adversarial-001

# 3. Run the task's local test harness
bash tests/test.sh

# 4. (Optional) Run full Harbor-style validation for this task
cd ..
python harbor_runner.py validate --task cobol_packed_decimal-adversarial-001

# 5. (Optional) Run the full LangGraph red-teaming pipeline
cd ..
python langgraph_red_team.py \
  --tasks cobol_packed_decimal-adversarial-001 \
  --weaknesses COBOL_PACKED_DECIMAL \
  --models gpt-4o claude-3.5-sonnet
```

If all goes well:

- `tests/test.sh` will report all variants as passing once you implement the solution.
- `harbor_runner.py validate` will emit a JSON summary with per‑stage status.
- `langgraph_red_team.py` will print where the final report was written under `./results`.

## Core Workflows

### 1. Generate adversarial tasks

The main entry point is [`adversarial_task_generator.py`](file:///c:/Users/tanne/Stellar/Red_Team_Project/adversarial_task_generator.py#L813-L860).

Basic usage:

```bash
python adversarial_task_generator.py \
  --weakness COBOL_PACKED_DECIMAL \
  --num-tasks 3 \
  --output-dir ./adversarial_tasks
```

Key flags:

- `--weakness` (required unless profiling a model)  
  - Weakness category, for example `COBOL_PACKED_DECIMAL`.
- `--num-tasks`  
  - How many tasks to generate; each becomes its own subdirectory like `cobol_packed_decimal-adversarial-001`.
- `--output-dir`  
  - Where to place generated tasks (defaults to `./adversarial_tasks`).
- `--use-starcoder`  
  - If set, uses StarCoder via Ollama to generate additional adversarial variants; otherwise, a built‑in fallback set of variants is used.

Each generated task directory includes:

- `instruction.md` – human‑readable description of the task.
- `task.toml` – structured Harbor/HarborForge task spec.
- `environment/Dockerfile` – container image for running the solution.
- `solution/solve.sh` – reference solution stub.
- `tests/test.sh` and `tests/test_outputs.py` – test harness and oracle.
- `tests/variant_*.input` and `tests/variant_*.expected` – adversarial inputs and expected outputs.

### 2. Profile a model's COBOL weaknesses

You can generate a JSON profile describing how a target model tends to fail on COBOL packed‑decimal tasks:

```bash
python adversarial_task_generator.py \
  --profile-model gpt-4o \
  --profile-n 10 \
  --profile-out ./.context/model_profiles
```

This writes `.json` files under `.context/model_profiles` with:

- `weakness_taxonomy` – categories of likely failures.
- `probes` – concrete snippet/probe examples.

### 3. Validate a single task end‑to‑end

Local bash test harness:

```bash
cd adversarial_tasks/cobol_packed_decimal-adversarial-001
bash tests/test.sh
```

Harbor‑style multi‑stage validation (static checks, Docker build, oracle run, verifier run):

```bash
python harbor_runner.py validate \
  --task adversarial_tasks/cobol_packed_decimal-adversarial-001
```

This writes detailed logs under `./.context/runs/<run-id>` and prints a JSON summary with per‑stage status and log paths.

### 4. Run the LangGraph red‑teaming pipeline

The orchestration entry point is [`langgraph_red_team.py`](file:///c:/Users/tanne/Stellar/Red_Team_Project/langgraph_red_team.py#L982-L1000).

Typical usage:

```bash
python langgraph_red_team.py \
  --tasks cobol_packed_decimal-adversarial-001 \
  --weaknesses COBOL_PACKED_DECIMAL \
  --models gpt-4o claude-3.5-sonnet
```

Important details:

- `--tasks` expects **task IDs**, not filesystem paths.  
  - For example: `cobol_packed_decimal-adversarial-001`, which corresponds to `./adversarial_tasks/cobol_packed_decimal-adversarial-001`.
- `--weaknesses` should match the weakness categories used when generating tasks (e.g., `COBOL_PACKED_DECIMAL`).
- `--models` lists the model IDs; they must match the models configured in `.cline/config.json` or be otherwise resolvable by your environment.
- `--no-models` can be used to exercise the pipeline structure without actually calling external models.
- `--regenerate-tests` and `--use-starcoder-variants` let you regenerate test variants for existing tasks, optionally using StarCoder via Ollama.

Pipeline outputs:

- Human‑readable reports under `./results`, one per task/weakness combination.
- Aggregated success rate data under `./.context` (for example, `.context/success_rates.json`).

## Common Pitfalls and How to Avoid Them

- Tasks fail at generation time:
  - Ensure Ollama is running if you use `--use-starcoder`.
  - Check `.cline/config.json` `problem_generator` section if host/model names do not match your setup.

- Model calls fail in the red‑team pipeline:
  - Verify `OPENAI_API_KEY` (and any other API key env vars referenced in `solver_models`) are set in your shell.
  - If using custom base URLs, make sure they are reachable from your machine.

- Harbor validations fail immediately:
  - Confirm Docker is installed and the Docker daemon is running.
  - Ensure the `harbor` CLI is installed and on your `PATH`, or set `HARBOR_CLI` to the full path.

- `tests/test.sh` fails or does not run on Windows:
  - Run the project inside WSL2 or use Git Bash; the test harness is a bash script.

## Development Utilities

For local quality checks you can use:

```bash
ruff .            # lint for runtime errors and common issues
pyright .         # basic static type checking
```

These use the configuration in `pyproject.toml` and `requirements-dev.txt`.
