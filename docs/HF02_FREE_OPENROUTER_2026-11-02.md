# HF-02 initial OpenRouter pilot â€” November 2, 2026

The owner replaced the initial paid proposal with a **zero-cost unified-provider
pilot**, authorized reuse of the active FCC OpenRouter setting, and requested
workspace guardrail changes. Paid inference, credit purchases and paid fallback
remain unauthorized. The earlier $15 proposal is historical and does not gate
these authorized free-only calls.

## Plan and model selection

Affected files: explicit free-only profile/configuration validation, conservative
request/campaign accounting, a guarded host transport, supported Harbor model
metadata, zero-cost preflight/launch scripts and focused regression tests.
Existing paid profile semantics, protected grading and immutable earlier evidence
remain intact. Risks are hidden SDK retries, mutable free availability, paid
fallback, account plugins/BYOK charges, response caching and confusing requests
with whole agent trials. Verification uses offline recording HTTP tests first,
then bounded actual free availability calls and a separate frozen calibration
matrix. No actual model outcome is inferred from a mock or catalog entry.

| Tier role | Exact OpenRouter model | Reason for selection |
| --- | --- | --- |
| Coding screen | `cohere/north-mini-code:free` | Small specialist for agentic coding; currently healthy free endpoint. |
| Balanced agent | `nvidia/nemotron-3.5-super-vl:free` | Larger reasoning/tool-capable agent model with a healthy free endpoint. Text only is used here. |
| Deeper reasoning | `nvidia/nemotron-3-ultra-550b-a55b:free` | Higher-capacity reasoning/orchestration profile for confirmation. |

These are provisional routing roles, not a measured universal ordering. All three
were present in the [official model catalog](https://openrouter.ai/api/v1/models),
with zero pricing in both catalog and exact endpoint metadata, and passed one
actual guarded availability request each at reported cost zero. The new Flint
free SKU was inspected but its endpoint was unavailable at selection time; it
was not silently substituted into an experiment.

The current model pages are [North Mini Code](https://openrouter.ai/cohere/north-mini-code:free),
[Super VL](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free) and
[Ultra](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free).
Availability may change; the guard stops instead of replacing a frozen model.

On November 5 the owner requested Flint. Its endpoint was healthy again, and a
separate [matched Flint supplement](HF02_FLINT_SUPPLEMENT_2026-11-05.md) reused
these exact tasks/harness without changing the November 2 experiment or receipt.
Flint completed 2/6 trials successfully at $0; the other four hit output/token
limits.

## Workspace and zero-cost controls

The existing default-workspace model guardrail was edited and saved: Cohere and
NVIDIA plus the three exact free variants were added. Seven prior model choices
were preserved. The UI now shows ten allowed models and nine allowed providers.
Existing authentication, privacy settings and unrelated application credentials
were not changed.

The active FCC key was read only by the controller. Its value is never copied
into tracked settings, task containers, model prompts or reports. Account checks
showed 1,000 free requests available initially; no credits were purchased. Neither
selected provider had a configured workspace BYOK credential. Response Healing
and Pareto defaults were off; Web/PDF overrides were unlocked. The one optional
preset is not selected by the pilot.

Each request pins a named `:free` SKU, verified endpoint provider tags, all-zero
provider price caps, no provider/model fallback and no SDK retry. Plain-text-only
requests forbid presets, server tools, paid plugins and response caching. The
same prompt-only JSON protocol is used across all models. Catalog/endpoint and
sanitized account evidence is hashed and freshness-checked before dispatch; the
actual serialized HTTP body is checked after SDK transformations. Cost comes
from the provider's numeric `usage.cost`, which must be exactly zero. Missing or
positive usage stops execution instead of receiving an invented zero estimate.

The [routing documentation](https://openrouter.ai/docs/guides/routing/provider-selection)
defines server-side price and fallback filters. Six schema-supported plugins are
explicitly disabled. The current beta Alignment schema has no valid disabled
form; it is omitted only with account-unconfigured/unforced proof, no preset
request and its [request-carried-rule contract](https://openrouter.ai/docs/guides/features/plugins/alignment).
The first availability attempt exposed that schema mismatch and was rejected
before inference; that failed attempt is preserved separately. Three subsequent
guarded availability calls passed at actual reported cost zero.

## Registered pilot and empirical boundary

The frozen design is three fresh calibration tasks, three model profiles and two
attempts each: **18 planned terminal-agent trials**, with a **$0 campaign cap**,
concurrency one, no automatic provider retries and at most 12 guarded requests
per trial (**216 inference requests** in the matrix). Availability probes are
recorded separately. Requests are spaced by at least four seconds within a trial;
the [provider quota](https://openrouter.ai/docs/api_reference/limits) is checked
before starting. Shared-account quota or provider errors remain visible.

Concrete task hashes, source/profile snapshots, prompt/parser hashes and limits
are preregistered before execution. Higher-tier models run the same fixed matrix
without adaptive promotion. This is calibration and infrastructure validation,
not untouched holdout measurement or evidence that a child task is harder.

The first real trial encountered a pre-inference trusted-tool setup failure;
its registry and uncertainty are retained. Fixes and subsequent campaign records
must preserve that observation rather than reuse the claimed campaign ID or
silently overwrite missing outcomes. Actual completed results are recorded in
the final source-bound receipt after execution.

## Completed actual free calibration

All 18 registered v3 trials completed with known accounting. The 102 actual
provider requests in this matrix reported zero cost, below its 216-request
ceiling. No model substitution, automatic retry, credit purchase or paid fallback
occurred. Three availability calls and two earlier protocol-development model
requests are separate; their provider generation records also show zero charges.

| Selected model | Registered outcomes | Actual model cost |
| --- | --- | ---: |
| North Mini Code | 0/6 completed; six request/output-budget timeouts | $0 |
| Nemotron 3.5 Super VL | 6/6 independently verified passes | $0 |
| Nemotron 3 Ultra | 6/6 independently verified passes | $0 |

Recommend Super VL as the initial default and Ultra for escalation. North remains
a weak baseline under this registered protocol, not the recommended default.
These are only three development/calibration task templates with two attempts per
model. This is not a universal ranking, a frontier-model claim, an independent
holdout study or proof that a mutation increased difficulty.

The [public-safe source-bound receipt](evidence/hf02-free-openrouter-2026-11-02.json)
contains all denominators, token totals, exact profile/model identities and
snapshot hashes. Raw task content, candidate source/output, provider traces and
SQLite registries remain in ignored private artifacts. No private calibration
bundle was exported publicly.

Earlier v1 setup failure and v2 accounting/protocol failure remain immutable.
The v2 original row still says provider error; an append-only correction links
the provider generation receipts proving $0 for both calls and showing the
reasoning-only 2,048-token length stop. V3 uses a new matched harness/profile,
8,192 tokens per call, complete known-response accounting, no implicit harness
retries and byte-identical tasks to v2. The new allowance is a protocol-development
change, not selective rerunning of wrong answers until they pass.

Actual Terminus setup also exposed UV-hardlinked trusted helper files and missing
recording bootstrap. The helper is now staged only after its exact pinned Harbor
RECORD checksum verifies; untrusted links still reject. Asciinema is preinstalled
and initialized during image build, preserving read-only/no-network execution.
The [setup receipt](evidence/hf01-terminus-setup-2026-11-02.json) records real setup,
recording and oracle/no-op controls with zero model calls.

Final local validation passed 362 Windows tests (two documented platform/explicit
setup skips), 363 Linux tests (one explicit setup skip) and Ruff. Current code
and pricing/account evidence were frozen through the live v3 matrix. Existing
GitHub Actions remain disabled; no remote CI run is claimed. The historical 12
public fixture packages and older release receipts remain unchanged.
