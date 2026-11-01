"""Harbor-imported Terminus subclass; all terminal execution stays in Harbor."""

from dataclasses import asdict

from harbor.agents.terminus_2.terminus_2 import Terminus2

from .call_budget import BudgetedLLM, CallBudget


class BudgetedTerminus2(Terminus2):
    def __init__(self, *args, campaign_budget: dict, per_call_output_tokens: int = 2048, **kwargs):
        self._campaign_budget = CallBudget(**campaign_budget)
        self._per_call_output_tokens = per_call_output_tokens
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
        return BudgetedLLM(super()._init_llm(*args, **kwargs), self._campaign_budget, self._per_call_output_tokens)

    async def run(self, instruction, environment, context):
        try:
            return await super().run(instruction, environment, context)
        finally:
            context.metadata = {**(context.metadata or {}), "budget_reservation": asdict(self._campaign_budget)}
