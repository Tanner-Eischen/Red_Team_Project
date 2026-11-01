# Legacy demonstrations

`langgraph_red_team.py` preserves the original prompt-only workflow for historical inspection.
It is not a terminal-agent evaluator, and its historical scores have not been validated.
The original graph's disconnected revision node is not used by HarborForge.

The supported root entrypoint delegates to the bounded campaign controller, which creates
immutable child tasks, reruns quality gates and matched trials, enforces budgets and never
allows discovery to consume holdout. LangGraph is optional authoring orchestration, not an
evaluation substrate; it is intentionally absent from the required runtime dependency set.

The original COBOL category remains an explicitly unverified demonstration in the legacy
generator. Other placeholder categories fail closed. Prefer `redforge task generate`.
