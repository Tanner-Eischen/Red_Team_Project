# Wave 3 â€” authorized container and offline work

Baseline: clean `codex/evaluation-platform`, `125872830a9445b79d22124d45a066d84aa73139`.
The owner requested container and offline work first, a commit, and a proposed
budget before any paid model runs. Model inference and external paid workers are
not authorized. No model call, provider authentication check, or automatic paid
campaign is part of this work.

## Plan before implementation

| Scope | Owning files | Verification |
| --- | --- | --- |
| HF-01 real runtime | renderer, quality gate, pinned adapter/environment, restricted verifier and runtime probes | Actual Harbor 0.13.2/native Linux Docker; all three families and adversarial boundary controls |
| HF-04/05 offline hardening | schema-2 policy, campaign controller, operator catalog and versioned fixture generation | All-pass to immutable child/revalidation/retest; evidence-dependent operator selection; bounded transitions; historical config/package preservation |
| HF-03/07/08 accounting and reports | budget guards, frozen preregistration, statistics and report export | Complete planned matrix, final-per-repeat accounting, timeout-inclusive completion, missingness, cohort/export rejection |
| Integration and next-run proposal | CLI, read-only budget arithmetic, documentation, offline workflows | End-to-end CLI refusal of public holdout export; exact budget calculations; full lint/tests, public bytes and package installation |

Dependencies: Python 3.12, locked Harbor 0.13.2, NumPy/rasterio, the already
running native Ubuntu/WSL Docker engine. Use only task-owned containers; leave
unrelated workloads untouched. Existing public examples and reports are
historical immutable evidence. Fresh generator versions need distinct identities.

Risks: hidden-case or reward leakage, unsafe artifact transfer, candidate process
persistence, ignored Harbor configuration, error-driven false hardening, repeated
or uncertain billing, adaptive holdout leakage and misleading report denominators.
Controls must fail closed. Scripted offline adapters demonstrate controller
behavior only; they are not model measurements or proof of harder tasks.

The budget proposal is a non-executable wrapper: approval remains
`proposed_not_authorized`, and the embedded campaign has no task paths.
`redforge campaign budget` performs arithmetic only and imports no provider or
runtime adapter. Current official model documentation supplies dated prices;
account-specific availability remains untested until an approved pilot preflight.

Final acceptance will record actual results, source and evidence hashes, runtime
limitations, commands, commits and the remaining paid/empirical gates. A clean
unit suite alone cannot close HF-01 or establish HF-02/06/07 model evidence.

## Review correction

Review caught that a total-attempt cap does not separately cap provider retries. The proposal now requires an explicit campaign-wide retry ceiling, distinct from the per-trial retry limit; legacy configurations retain their previous default. Suggested AGENTS.md addition: declare and test global and per-trial retry budgets independently, including known-usage provider errors that leave primary trials pending. This is a proposed documentation rule, not an additional approval gate.

## Completed container/offline stage

- Native Harbor 0.13.2/Docker 29.1.3 passed 18 baseline controls across all three new families, seven semantic ETL-child controls, eight distinct adversarial probes and three complete agent-image layer audits. A final protected-namespace cleanup probe also passed. Missing verifier Dockerfile and GeoTIFF libexpat were reproduced and fixed.
- Final public-contract retake repeated the 25 controls and three layer audits. The last read-only-workspace sentence is a disclosure-only delta: private fixtures, scorer, Dockerfiles and controls are identical. The exact tested and final hashes are in [the contract addendum](evidence/hf01-contract-addendum-2026-11-01.json). New hashes must receive fresh validation before model execution; old eligibility is not copied.
- Windows full suite: 220 passed, one POSIX-only skip (142.31 seconds). Linux full suite: 218 passed, including that POSIX cleanup test (50.6 seconds), before the disclosure-only generator update. The final three-family public-contract and 14 budget/CLI checks passed again on current source. Ruff passed.
- The final core wheel installed in a separate environment without Harbor/geo extras and executed the read-only budget command under Python isolated mode. Current-source and artifact hashes are bound in [the integration receipt](evidence/wave3-offline-2026-11-01.json).
- All 12 historical public packages and their prior report are unchanged. Original runtime receipts remain immutable; raw test/image evidence stays in the native cache or ignored artifacts directory. Owned containers and keepers are cleaned up.

HF-04/05 now implement all-pass hardening, six evidence-linked semantic mutations, append-order review revocation, semantic-no-op rejection, immutable matched children and explicit schema migration. HF-03/07/08 account for global retries, concurrent request envelopes, final-per-repeat completion, missing planned cells and private-cohort export refusal. Injected tests remain explicitly labeled as zero real-model measurement.

**No evaluation model/provider requests or model spending occurred.** HF-02 real trials, HF-06 empirical parent/child confirmation and HF-07 independent model measurement remain unrun. The concrete next proposal is a $15 maximum for 12 primary pilot attempts and at most two provider-error retries; larger experiments are not included. Model account access and availability are not asserted. No paid campaign is scheduled or automatically resumed.

Remote GitHub Actions are currently disabled for this repository. The existing setting is preserved; the offline workflow and three-family manual container matrix are prepared, but no remote CI run is claimed. Actual verification above ran locally on Windows and native Linux/Docker.

## Reproduce and rollback

Use the locked development environment and run `ruff check .`, `pytest`, and `python scripts/check_public_tasks.py`. `python scripts/run_container_controls.py --output-dir artifacts/new-container-controls` creates a new evidence directory and invokes only actual Docker controls. `redforge campaign budget --proposal docs/proposals/hf02-pilot-2026-11-01.json` is read-only and cannot authorize or launch a model.

No registry schema migration is required. Historical schema-1 behavior is retained; schema-2 policy is opt-in and changes require new campaign/preregistration IDs. A rollback reverts code while retaining immutable task packages, execution claims, trial rows, unresolved reservations and evidence; never reset an interrupted campaign's spending or reuse its ID. Native runtime evidence documents the tested boundary, not a universal Docker/kernel security proof.
