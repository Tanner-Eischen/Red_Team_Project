# HF-03: local budget-accounting hardening

## Plan before implementation

Baseline: `f6e9cddbb2220f2fcf3e05b938b6013cc65a7eeb`, clean
`codex/evaluation-platform` checkout. User authorized continued local execution
after committing the preceding project work. No model profiles, paid requests,
Docker/UI operation or publication are authorized by this change.

Reproduced offline: settling one campaign reservation twice changed its cost
from approximately 0.2 to -0.6 and input usage to -60. A fake provider reporting
negative input tokens also received two calls while usage remained marked known.

Affected files: `execution/budget.py`, `adapters/call_budget.py`, campaign result normalization, the Harbor error
classifier, and their focused regression tests. Add issued reservation identities,
single settlement, atomic in-process accounting, strict token/usage checks and
a distinct provider-usage error. Existing campaign claiming and historical
configuration semantics remain in place. No registry migration is proposed.

Verification: duplicate/foreign/forged handles, unknown and fractional usage,
over-envelope billing, concurrent reservations/settlements, timeout arguments,
provider follow-up refusal and classification. Run Ruff, focused checks, the
full offline suite and public-task byte verification. Use recording fixtures
only; local tests do not establish Docker isolation or model performance.

Risks/limits: this is an in-process accounting repair. It does not implement
durable cross-campaign reconciliation, exactly-once external billing, new billing
dimensions or runtime isolation. HF-01 container controls and HF-02 approved
real-model trials remain separate gates. Existing one-time campaign claims must
continue to reject silent restart after interrupted work.

## Implemented and verified

Issued reservation handles can settle once in their originating ledger; duplicate,
foreign and changed handles reject without crediting funds. Unknown accounting
retains the full reservation. In-process concurrent reserve/settle operations are
serialized. Token counts and attempt timeouts use explicit integer contracts.

Per-request provider usage rejects negative, fractional, nonfinite, missing and
unrepresentable values, marks accounting unresolved and blocks follow-up calls.
A separate ProviderUsageError maps to PROVIDER_ERROR rather than a model-capability
timeout. Campaign normalization records a malformed usage trial with unknown
accounting and stops, rather than overflowing before the trial is retained.

Independent source review found the huge-integer overflow case after the first
pass; that case was reproduced and fixed in the provider, campaign and ledger
guards. The exact fake-provider witness now makes only one call, and replayed
settlement preserves the measured balance.

Final verification: 124 offline tests passed (402 existing Affine deprecation
warnings), 75 focused checks passed, full Ruff lint passed, and all 12 immutable
public task packages remain valid. Compact source-bound evidence is in
[evidence/hf03-budget-2026-11-01.json](evidence/hf03-budget-2026-11-01.json).
Raw local logs remain under artifacts/hf03-budget-2026-11-01-final/.

No model calls, paid requests, container runs, browser/UI operations or external
publication occurred. This advances the local HF-03 accounting gate only.
No registry/data migration is required; rollback is limited to these source/test
files while retaining existing registries and reservations for reconciliation.

Suggested future AGENTS addition: treat provider accounting as untrusted numeric
input, make campaign reservation handles single-use, and regression-test malformed
usage all the way through trial retention and the follow-up-call guard.
