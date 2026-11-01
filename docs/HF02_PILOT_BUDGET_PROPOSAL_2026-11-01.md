# HF-02 pilot budget proposal — November 1, 2026

**Proposed only. No paid model execution is authorized or performed.**
The owner requested this proposal after container/offline work and before spending.

The first paid stage would run three discovery tasks, one per scientific family,
against two dated model snapshots with two fresh attempts each: **12 planned
attempts**. Both profiles use the same terminal harness and resource envelopes.
This is an infrastructure pilot, not an independent model ranking.

| Proposed model | Standard input / output USD per million tokens | Attempts | Maximum token-envelope cost per attempt | Reservation per attempt |
| --- | --- | ---: | ---: | ---: |
| `gpt-5.4-mini-2026-03-17` | $0.75 / $4.50 | 6 | $0.336 | $0.35 |
| `gpt-5.4-2026-03-05` | $2.50 / $15.00 | 6 | $1.120 | $1.20 |

Prices and snapshot identifiers were checked against official
[Mini documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
and [GPT-5.4 documentation](https://developers.openai.com/api/docs/models/gpt-5.4).
These are text-only standard global API assumptions, without cache discounts,
Batch/Flex discounts, regional premiums or paid hosted tools. Local Docker tools
do not use OpenAI-hosted containers. Account availability has not been queried;
unavailable snapshots must stop rather than silently change the model.

Each attempt has a cumulative **256,000 input / 32,000 output** token envelope,
including full-history request reservations and reasoning output where reported.
The input ceiling stays below GPT-5.4's documented long-context threshold.
The token-envelope calculation is **$8.736 for the 12 primary attempts**.
Primary reservations total **$9.30**. At most two additional provider-error
attempts reserve up to **$2.40**, giving **$11.70 total reservations**.

**Recommended campaign cap: $15, maximum 14 total attempts, concurrency one.**
The remaining $3.30 does not authorize more attempts, authorship calls, model
substitution or task revisions. Wrong answers are not retried; unresolved usage
stops the campaign for reconciliation. Time limits are 600 seconds for the agent,
1,200 seconds per trial and six hours for the campaign including controls.
These configured-price ceilings are not measured bills or a guarantee about
provider billing, taxes, future price changes or unsupported billing dimensions.

The [machine-readable proposal](proposals/hf02-pilot-2026-11-01.json) is deliberately
not executable: it remains `proposed_not_authorized` and has no task paths.
Its arithmetic can be checked without contacting a provider or creating a registry:

```powershell
redforge campaign budget --proposal docs/proposals/hf02-pilot-2026-11-01.json
```

After approval, concrete task, scorer, image, prompt/tool and model/harness
identities must be pinned in a new campaign before the first paid request.
All cells, retries, errors and unused slots will remain visible. Parent/child
confirmation (HF-06) and the proposed larger frozen measurement (HF-07) require
separate budget proposals after inspecting pilot outcomes; they are not included
in this $15 request.
