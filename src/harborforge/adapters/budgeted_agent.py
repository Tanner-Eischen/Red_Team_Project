"""Harbor-imported Terminus subclass; all terminal execution stays in Harbor."""

from dataclasses import asdict
import time

from harbor.agents.terminus_2.terminus_2 import Terminus2

from .call_budget import BudgetedLLM, CallBudget


class BudgetedTerminus2(Terminus2):
    def __init__(self, *args, campaign_budget: dict, per_call_output_tokens: int = 2048,
                 free_routing: dict | None = None, **kwargs):
        self._campaign_budget = CallBudget(**campaign_budget)
        self._per_call_output_tokens = per_call_output_tokens
        self._free_routing = free_routing
        self._free_llm = None
        if self._campaign_budget.max_cost_usd == 0 and free_routing is None:
            raise ValueError("Zero-cost live model agents require verified free-only routing")
        if free_routing is not None:
            if any(value != 0 for value in (self._campaign_budget.max_cost_usd,
                    self._campaign_budget.input_usd_per_million, self._campaign_budget.output_usd_per_million)):
                raise ValueError("Free-only agent requires exact zero price and cost ceilings")
            if kwargs.get("api_base") not in (None, "https://openrouter.ai/api/v1"):
                raise ValueError("Free-only agent cannot override the OpenRouter API origin")
            from .openrouter_free import trusted_free_model_info
            model_name = kwargs.get("model_name", args[1] if len(args) > 1 else None)
            trusted = trusted_free_model_info(model_name, free_routing)
            if kwargs.get("model_info") not in (None, trusted):
                raise ValueError("Free-only metadata must match the verified catalog/endpoints")
            kwargs["model_info"] = trusted
        if kwargs.get("llm_backend", "litellm") != "litellm" or kwargs.get("use_responses_api", False):
            raise ValueError("Budget guard currently supports LiteLLM chat completion only")
        if kwargs.get("llm_kwargs"):
            raise ValueError("Custom LLM constructor settings have no verified budget contract")
        call_settings = kwargs.get("llm_call_kwargs") or {}
        if set(call_settings) - {"max_tokens", "temperature", "seed", "top_p", "timeout"}:
            raise ValueError("Unsupported LLM call settings could bypass the configured output envelope")
        kwargs.update(collect_rollout_details=False, use_responses_api=False)
        super().__init__(*args, **kwargs)

    def _init_llm(self, *args, **kwargs):
        inner = super()._init_llm(*args, **kwargs)
        if self._free_routing is not None:
            from .openrouter_free import OpenRouterFreeLLM
            inner = OpenRouterFreeLLM(inner, kwargs["model_name"], self._free_routing)
            self._free_llm = inner
        return BudgetedLLM(inner, self._campaign_budget, self._per_call_output_tokens)

    async def _query_llm(self, chat, prompt, original_instruction="", session=None):
        if self._free_routing is None:
            return await super()._query_llm(chat, prompt, original_instruction, session)
        # Pinned Terminus retries arbitrary errors with tenacity and recursively
        # reissues truncated/context failures. The registered free-only harness
        # has zero automatic retries: retain the original accounted outcome.
        started = time.monotonic()
        response = await chat.chat(prompt, **self._llm_call_kwargs)
        self._api_request_times.append((time.monotonic() - started) * 1000)
        return response

    async def run(self, instruction, environment, context):
        try:
            return await super().run(instruction, environment, context)
        finally:
            context.metadata = {**(context.metadata or {}), "budget_reservation": asdict(self._campaign_budget)}
            if self._free_llm is not None:
                requests, audit = self._free_llm.requests, self._free_llm.audit
                verified = (type(requests) is int and requests >= 0 and isinstance(audit, list)
                    and requests == len(audit) and not self._campaign_budget.usage_uncertain
                    and all(isinstance(row, dict) and row.get("pricing_evidence_verified") is True
                        and type(row.get("provider_cost_usd")) in (int, float) and row["provider_cost_usd"] == 0
                        and all(type(row.get(key)) is int and row[key] >= 0
                                for key in ("prompt_tokens", "completion_tokens")) for row in audit))
                if verified:
                    # Pinned Terminus2.run replaces a real zero charge with None.
                    # Restore only directly observed complete guard receipts,
                    # including summarization calls through the same wrapper.
                    context.cost_usd = 0.0
                    context.n_input_tokens = sum(row["prompt_tokens"] for row in audit)
                    context.n_output_tokens = sum(row["completion_tokens"] for row in audit)
                context.metadata["free_only"] = {"requests": requests, "provider_usage": audit,
                                                  "usage_verified": verified,
                                                  "last_failed_response_meta": getattr(
                                                      self._free_llm, "last_failed_response_meta", None)}
