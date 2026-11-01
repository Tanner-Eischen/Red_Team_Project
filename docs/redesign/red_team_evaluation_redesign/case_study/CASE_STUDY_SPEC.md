# Case Study Specification: Evaluating Agentic Scientific Python Engineering

**Status:** Proposed. Not a completed benchmark or a claim of measured model performance.

## Research question

Under a fixed terminal-agent harness, how reliably do different model families complete scientific Python development tasks that require exact data semantics, numerical correctness and reproducible outputs? Which failures emerge from tool use, parsing, units, control flow, and mistaken assumptions? Can controlled task mutations reveal stable generalizable weak points without making problems invalid?

## Benchmark design

Create a **12-task pilot**, four tasks in each of three families, with varied difficulties based on task complexity and actual engineering work, **not merely empirical model failure**.

| Family | Example engineering task | Hidden evaluation emphasis |
|---|---|---|
| `scientific_etl` | Repair Python CSV/Parquet pipeline for sensor measurements, nullable status flags and duplicate timestamps | Correct null policy, units, aggregation, idempotence, schema |
| `geospatial_raster` | Combine two misaligned GeoTIFFs with distinct CRS, NoData and pixel size | Correct coordinate transform, pixel alignment, masking, boundary behavior |
| `repository_debug` | Fix a broken Python API or TypeScript ETL utility after reading repository and tests | Behavioral correctness, minimal regression, API compatibility, edge cases |

Sample public fixtures should be very small and bundled with tasks. Avoid download or web availability dependencies during grading. Preserve more challenging independent tasks for the held-out measurement suite.

## Experimental cohorts

- **Discovery:** three task families with controllable mutations. Each mutation includes a rationale and independent validation.
- **Calibration:** a few known-pass, known-fail control tasks to confirm harness and scoring.
- **Holdout:** new task seeds and scenarios developed without inspecting target models' earlier task responses. Ideally review independently before freezing.

Every task must declare requirements, oracle, independent verifier, negative controls, outcome artifacts, pinning and hashes. For numerical tasks, define coordinate conventions, units, NoData behavior and tolerances. No dataset should be fabricated and described as a real scientific source.

## Evaluation experiment

### Minimum pilot

- 2–3 fully validated tasks.
- One low-cost and one mid-capability model, with 2 repeats each, matched terminal harness.
- Record complete trajectories and tester/runner statuses.
- Validate one failure with human review and one model success with full evidence.

### Expanded showcase

- 12 validated tasks, 4 models from multiple families, 3 repeats each = **144 trials**, not including all candidate generation and screening.
- Same pinned environment, same agent scaffolding and tool contract.
- Use a preregistered held-out subset and report separate discovery/holdout results.
- Cost/time budget must be approved and configured before paid calls. No blanket promise that 144 trials are affordable.

### Example target scenario

**Scientific ETL task: nullable flag and unit-conversion trap**

Public requirements:
- Input table includes `station_id`, `timestamp`, `value`, `unit`, `status`.
- Unit aliases are explicitly documented.
- Missing status is not zero or valid by default. Duplicate timestamps have a specified deterministic resolution rule.
- Emit a canonical Parquet/JSON summary with prescribed keys, dtype and sorted order.
- Must work on more than one input file and tolerate nonuniform timestamps.

Hidden verifier test groups:
- Basic known-good table.
- Unit conversion with nontrivial scaling.
- Duplicate timestamps near a daylight saving or day-boundary situation, if specification defines timezone semantics unambiguously.
- Missing status values and negative/NaN values with explicit contract.
- Inputs with shuffled order, extra irrelevant columns and missing optional fields.
- Regression tests for previously valid outputs.

Mutations:
- `M1` adds a second source and cross-source duplicate resolution.
- `M2` adds a documented "quality overrides reading" rule, requiring correct dependency order.
- `M3` adds a new unit type but leaves supported conversions fully defined.

Each child is evaluated against its *own* correct oracle after independent controls pass. The objective is to test multi-step reasoning and robustness, not to withhold essential definitions.

## Deliverables that a hiring manager can actually inspect

1. **Repository:** clean installs, reproducible commands, task family source, hidden-test isolation, CI and stable run artifacts.
2. **Methods:** scope and exclusions, data split policy, model/harness configs, validation gates, scoring logic and uncertainty.
3. **Task manifest:** versioned parent and child with mutation hypotheses, hashes, oracle/no-op/wrong-solution evidence.
4. **Results matrix:** model × task × attempt, including scorer reward, infra errors, repeatability and cost.
5. **Failure deep dive:** timeline of commands and file diffs, verifier evidence, root-cause hypothesis, reviewed classification and an actually tested task revision.
6. **Static visual report:** confidence intervals, task lineage and validation funnel, plus links to individual trial records.
7. **Limitations:** adaptive selection, task distribution, model/harness differences, API model drift, statistical uncertainty and holdout contamination risks.

## Definition of done

- No hidden reference implementation or tests in any model-visible environment image/workspace.
- Oracle succeeds and no-op/known wrong answers fail with correct reward files.
- Running a single known task against at least two actual configured models yields traced, reproducible scored attempts.
- Positive and negative controls are part of CI, model-funded campaigns are optional and budget-capped.
- Failure classification is supported by real tool or verifier evidence, not an invented story.
- Report includes model IDs, denominator, evaluated tasks and dates, and does not claim broad benchmark validity for a small sample.
- At least one controlled revision is demonstrated with parent-child comparison under unchanged conditions.
- A frozen separate cohort exists or publication expressly labels the results as exploratory discovery only.
