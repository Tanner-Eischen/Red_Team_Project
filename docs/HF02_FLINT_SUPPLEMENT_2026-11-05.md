# HF-02 Flint supplement: November 5, 2026

The owner requested adding Flint to the initial free OpenRouter pilot. This is a
new six-trial supplement to the [November 2 calibration](HF02_FREE_OPENROUTER_2026-11-02.md),
using its exact three immutable task versions and two repeats per task. Earlier
trials and receipts remain unchanged. The comparison spans two dates and is
development/calibration evidence, not an independent holdout or general ranking.

## Selection and matched protocol

The official [Flint model page](https://openrouter.ai/recursive/flint-v1.0:free)
and freshly retrieved catalog/endpoint API identified `recursive/flint-v1.0:free`,
canonical snapshot `recursive/flint-v1.0-20261021`. The selected endpoint was
healthy (status zero) and all reported price dimensions were zero. Its exact
provider endpoint slug is `recursive/bf16`. The workspace already allowed Flint
and Recursive, so this supplement made no workspace configuration changes.

The provider-tag validator now accepts bounded slash-separated endpoint slugs,
as specified in [OpenRouter's provider routing contract](https://openrouter.ai/docs/guides/routing/provider-selection#targeting-specific-provider-endpoints).
It retains the full slug in `provider.only` and separately verifies absence of
BYOK credentials for the base provider `recursive`. A variant tag alone cannot
establish that its provider has no BYOK key. Malformed slugs still reject.

`scripts/prepare_free_pilot.py --flint-supplement-from REFERENCE_CAMPAIGN`
verifies the old frozen suite before reusing its existing task directories. It
freezes a new campaign and suite with a hashed reference link. Task hashes,
Harbor 0.13.2, prompt/parser hashes, temperature zero, low reasoning, 8,192 output
tokens per call, cumulative token bounds and timeouts match the reference.
Flint runs all six cells without adaptive selection. Its campaign permits at
most 72 provider requests, concurrency one, no retries and exactly $0 spending.

Fresh authenticated account checks confirmed Recursive BYOK was not configured,
BYOK-only routing was off, paid plugin defaults were off or explicitly
overridable, and no paid moderation classifier was configured. The existing
controller-only FCC key is reused privately. Each actual request keeps the
named free SKU, exact endpoint, zero price caps, disabled plugins, no fallback,
no response cache and verified provider usage accounting. Metadata/account
proof expires after one hour. No credits or paid inference are authorized.

## Results and validation

All six registered trials completed with known accounting: **two independently
verified passes and four unsuccessful completions**, recorded as `AGENT_TIMEOUT`.
Three stops hit the 8,192-token per-response output limit; one exhausted the
cumulative token envelope before another model call. They were not four wall-time
timeouts. The two repository-debug trials passed; both geospatial and both ETL
trials stopped at those limits.

The matrix made **36 actual provider requests at $0**, below its 72-request
ceiling. Its separate availability probe made one additional request at $0.
All provider receipts were verified, with no missing usage, automatic retry,
model substitution, credit purchase or paid fallback. Native provider usage
totalled 187,365 input tokens and 123,992 output tokens. All three task quality
suites passed actual Docker controls before the corresponding model trials.

| Model and run date | Completed successfully | Other outcomes |
| --- | ---: | --- |
| North Mini Code, November 2 | 0/6 | Six token/output-budget stops |
| Super VL, November 2 | 6/6 | None |
| Ultra, November 2 | 6/6 | None |
| Flint, November 5 | 2/6 | Three per-response output stops, one cumulative-token stop |

Flint is now an evaluated coding baseline. Super VL remains the recommended
initial default within this small pilot. The results do not establish a broad
model ranking, and the limits were not expanded after observing failures.

The [source-bound public receipt](evidence/hf02-flint-openrouter-2026-11-05.json)
records exact model/configuration/source hashes, per-family counts and token
totals. Source, task bytes, prompt/parser hashes and harness settings were
unchanged through the supplement. Raw tasks, candidate artifacts, model traces,
account snapshots and registry remain in ignored private artifacts. An exact-key
scan of 653 private output files found no credential copies. Public fixture
packages and historical reports are unchanged.

Full local validation passed 388 Windows tests (two platform/explicit setup skips)
and 389 Linux tests (one explicit setup skip), plus Ruff. The skipped native
setup-only probe was not enabled; actual scored runs and task controls exercised
Docker independently. No remote CI run is claimed. Owned runtime containers were
cleaned up after completion.
