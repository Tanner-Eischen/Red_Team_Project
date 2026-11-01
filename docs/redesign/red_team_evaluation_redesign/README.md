# Red Team Project: Adversarial Evaluation Platform Redesign

**Design review:** 2026-10-31  
**Repository reviewed:** https://github.com/Tanner-Eischen/Red_Team_Project  
**Commit audited:** `34ffd34dd4ac7f59586abb5cf51087f0ea3da65e` (main, 2026-01-05)  
**Status:** Proposed implementation. No repository edits or paid model experiments were performed.

## What is in this package

| Document | Purpose |
|---|---|
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Main findings, architecture decisions, phased engineering plan, validation criteria, and tradeoffs |
| [architecture/SYSTEM_DESIGN.md](architecture/SYSTEM_DESIGN.md) | Subsystems, interfaces, run/state transitions, sandbox and data isolation |
| [protocols/EVALUATION_PROTOCOL.md](protocols/EVALUATION_PROTOCOL.md) | Task validity, model comparisons, selection bias, repeats, statistics, quality and failure adjudication |
| [schemas/task_manifest.example.yaml](schemas/task_manifest.example.yaml) | Proposed task provenance/lineage contract (supplements canonical Harbor task format) |
| [schemas/campaign.example.yaml](schemas/campaign.example.yaml) | Example tiered evaluation campaign (model IDs are placeholders) |
| [schemas/registry.sql](schemas/registry.sql) | Draft SQLite registry schema for reproducible experiments |
| [backlog/implementation_backlog.csv](backlog/implementation_backlog.csv) | Prioritized PR-level backlog with file ownership, tests and acceptance criteria |
| [case_study/CASE_STUDY_SPEC.md](case_study/CASE_STUDY_SPEC.md) | Specific portfolio-quality scientific software evaluation study |
| [case_study/REPORT_TEMPLATE.md](case_study/REPORT_TEMPLATE.md) | Report structure with separate development and held-out results |

## Key decision

**Do not build a replacement Harbor executor.** Make Harbor the primary adapter for sandboxed coding-agent trials and use Inspect AI as an optional companion for answer-based and tool-use evals. Build the distinctive custom layer: task generation, validity gates, experimental control, versioned trials, failure analysis, mutation, and publication.

## Critical first correction

The existing renderer copies `solution/` and `tests/` into the agent Docker image, and generated `tests/test.sh` runs the solution directly. Repair this before interpreting any model failure statistics. Tests should run as a verifier over agent-produced artifacts, and oracle and negative controls should be evaluated separately.

## Sources

- Harbor task format: https://github.com/harbor-framework/harbor/blob/main/docs/content/docs/tasks/index.mdx
- Harbor task authoring: https://github.com/harbor-framework/harbor/blob/main/skills/create-task/SKILL.md
- Inspect AI: https://inspect.aisi.org.uk/
- Inspect eval sets: https://inspect.aisi.org.uk/eval-sets.html
- Inspect scorers: https://inspect.aisi.org.uk/scoring.html
- Inspect scanners: https://inspect.aisi.org.uk/scanners.html
- AISI Engineering Playbook: https://engineering-playbook.aisi.org.uk/evaluate.html
- OpenAI, benchmark contamination and evaluation integrity (2026): https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ and https://openai.com/index/separating-signal-from-noise-coding-evaluations/
