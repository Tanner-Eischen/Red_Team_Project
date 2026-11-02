"""Explicitly authorized, bounded $0 availability probes through the production guard."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

from harborforge.config import load_campaign


def load_existing_key(path: Path) -> None:
    """Read one owner-authorized setting; never copy the file or disclose its value."""
    contents = path.read_text(encoding="utf-8-sig")
    match = re.search(r"^\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.*?)\s*$", contents, re.M)
    if not match:
        raise ValueError("Active settings contain no OpenRouter credential")
    value = match[1].strip().strip("\"'")
    if not value.startswith("sk-or-") or any(character.isspace() for character in value):
        raise ValueError("Active setting is not an OpenRouter credential")
    os.environ["OPENROUTER_API_KEY"] = value
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LITELLM_LOG"] = "ERROR"


async def probe(config, output: Path) -> dict:
    from harborforge.adapters.call_budget import BudgetedLLM, CallBudget
    from harborforge.adapters.harbor_adapter import redact
    from harborforge.adapters.openrouter_free import OpenRouterFreeLLM

    results = []
    for model in config.model_profiles:
        if results:
            await asyncio.sleep(4)
        policy = model.free_routing.model_dump(mode="json")
        free = OpenRouterFreeLLM(SimpleNamespace(_temperature=0, _reasoning_effort="low"), model.model_id, policy)
        budget = CallBudget(32000, 2048, 0, 0, 0)
        guarded = BudgetedLLM(free, budget, 2048)
        nonce = uuid.uuid4().hex
        row = {"model_id": model.model_id, "tier": model.tier, "started_utc": datetime.now(timezone.utc).isoformat(),
               "scope": "Availability/format probe only, not a benchmark task or model ranking"}
        try:
            answer = await guarded.call("Return exactly the JSON object {\"ok\": true}. Probe nonce: " + nonce,
                response_format={"type": "object", "properties": {"ok": {"type": "boolean"}},
                                 "required": ["ok"], "additionalProperties": False})
            parsed = json.loads(answer.content)
            row.update(status="passed" if parsed == {"ok": True} else "format_mismatch",
                       response_model_id=answer.model_name, usage=answer.usage.model_dump(mode="json"),
                       provider_requests=free.requests, provider_audit=free.audit)
        except Exception as error:
            row.update(status="error", error_type=type(error).__name__, error=redact(str(error)),
                       provider_requests=free.requests, provider_audit=free.audit,
                       accounting_uncertain=budget.usage_uncertain)
        row["finished_utc"] = datetime.now(timezone.utc).isoformat()
        results.append(row)
        (output / "availability.json").write_text(json.dumps({"results": results}, indent=2) + "\n",
                                                  encoding="utf-8", newline="\n")
        print(json.dumps({"model_id": model.model_id, "status": row["status"],
                          "provider_requests": row["provider_requests"]}), flush=True)
        if row["status"] != "passed":
            break
    return {"results": results, "all_passed": len(results) == len(config.model_profiles)
            and all(row["status"] == "passed" for row in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = load_campaign(args.campaign)
    if config.budgets.campaign_usd_max != 0 or not config.model_profiles or any(
        profile.billing_mode != "free_only" for profile in config.model_profiles
    ):
        raise ValueError("Only the explicitly zero-cost campaign may be probed")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    load_existing_key(args.key_file)
    result = asyncio.run(probe(config, args.output_dir))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
