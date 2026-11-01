# Experimental Report Template: Adversarial Scientific Software Evaluation

> **Template only:** Populate from real campaign artifacts. Never infer results from synthetic smoke tests or from task descriptions.

## Metadata

- Study ID: **TBD**
- Execution dates (UTC): **TBD**
- Repository commit and evaluation code version: **TBD**
- Campaign configuration hash: **TBD**
- Suite manifest hash: **TBD**
- Harbor and Docker versions: **TBD**
- Evaluated protocol: `terminal_agent` or `qa_completion`
- Tool and prompt parity exclusions: **TBD**
- Public source/replication link: **TBD**

## Research question and preregistration

- Target capability and domain: **TBD**
- Task family and selection scheme: **TBD**
- Discovery, calibration and holdout task counts: **TBD**
- Models, repeats, token/time budgets and tool sets chosen before holdout: **TBD**

## Quality gate evidence

| Gate | Passed / total | Comments / evidence |
|---|---|---|
| Structural/schema | TBD | TBD |
| Clean Docker build | TBD | TBD |
| Oracle positive | TBD | TBD |
| No-op negative | TBD | TBD |
| Known-bad negative | TBD | TBD |
| Alternative valid solution | TBD | TBD |
| Leakage scan | TBD | TBD |
| Human review | TBD | TBD |

## Task-selection funnel

| Stage | Count | Main reasons for exclusion |
|---|---:|---|
| Generated candidate versions | TBD | |
| Invalidated or quarantined | TBD | |
| Quality-approved candidates | TBD | |
| Tier 1 screened | TBD | |
| Tier 2 confirmed | TBD | |
| Tier 3 confirmed | TBD | |
| Independent holdout tasks | TBD | |

## Scored results (held out only for unbiased measurement claims)

| Model ID and snapshot | Valid tasks | Eligible trials | Pass trials | Pass rate + interval | Infrastructure errors | Median token use | Measured cost |
|---|---:|---:|---:|---|---:|---:|---:|
| TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Per-task data, trial IDs, exclusions and repeat structure are provided as `trials.csv`, `tasks.jsonl` and event logs.

## Discovery-only adversarial results

- Candidate mutation count and lineages: **TBD**
- Failure patterns, with evidence trails: **TBD**
- Parent-child outcomes under matched harness: **TBD**
- Confirmation samples and uncertainty: **TBD**
- Caution: These task failure rates are conditioned on adaptive selection.

## One detailed failure case

- Task version and SHA: **TBD**
- Capability mechanism being tested: **TBD**
- Prompt and visible task contract: **TBD**
- Agent behavior: **TBD**
- Artifact diff: **TBD**
- Verifier evidence: **TBD**
- Root-cause adjudication and reviewer confidence: **TBD**
- Alternate explanation / suspected harness artifact: **TBD**
- Reproduction link: **TBD**
- Revision and independent retest: **TBD**

## Limitations

- Adaptive selection bias: **TBD**
- Task distribution and coverage: **TBD**
- Model version drift: **TBD**
- Agent-scaffold and tool constraints: **TBD**
- Statistical uncertainty/sample size: **TBD**
- Potential contamination and test leakage: **TBD**
- Reviewer disagreement and unscorable results: **TBD**

## Reproduce

```powershell
# Replace with exact tested commands and dependency versions for the selected release.
# Never include API keys, hidden benchmark files, or private grader content.
```
