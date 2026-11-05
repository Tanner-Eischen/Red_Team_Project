from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from harborforge.adapters.call_budget import BudgetedLLM, CallBudget, ProviderUsageError
from harborforge.adapters.openrouter_free import FreeOnlyRoutingError, OpenRouterFreeLLM, verify_free_evidence

MODEL = "openrouter/recording/terminal:free"
SLUG = "recording/terminal:free"


@pytest.fixture
def policy(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    model = {"id": SLUG, "canonical_slug": "recording/terminal-20261102", "pricing": {"prompt": "0", "completion": "0"},
             "context_length": 100000, "supported_parameters": ["max_tokens", "temperature", "seed", "top_p", "reasoning"]}
    endpoint = {"model_id": SLUG, "status": 0, "tag": "recording", "context_length": 100000,
                "max_completion_tokens": 8000, "supported_parameters": model["supported_parameters"],
                "pricing": {"prompt": "0", "completion": "0", "request": "0", "discount": 0}}
    account = {"verified_at_utc": now, "byok_present": False, "paid_plugins_enabled": False,
               "byok_only_routing": False, "forced_paid_plugins": False, "default_plugin_overrides_locked": False,
               "server_tools_requested": [], "byok_providers": {"recording": "Not configured"},
               "plugins_disabled": ["web", "file-parser", "response-healing", "armor", "pareto-router", "context-compression"],
               "plugins": {"alignment": {"default_enabled": False, "prevent_overrides": False, "source": "recorded off"}}}
    paths = {}
    for key, value in [("catalog", {"data": [model]}), ("endpoints", {"data": {"id": SLUG, "endpoints": [endpoint]}}),
                       ("account_evidence", account)]:
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(value))
        paths[key + "_path"] = str(path)
        paths[key + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"policy_version": "openrouter-free-only-v1", "allowed_model_ids": [MODEL], **paths,
            "verified_at_utc": now, "max_age_seconds": 3600, "allow_fallbacks": False,
            "allow_paid_fallback": False, "max_usd_per_request": 0,
            "provider_max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
            "response_model_ids": [SLUG, "recording/terminal-20261102"],
            "max_requests_per_attempt": 2, "minimum_request_interval_seconds": 4}


def response(**usage):
    return {"id": "recorded-id", "model": SLUG, "choices": [{"message": {"role": "assistant", "content": "{}"},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 4, "cost": 0, **usage}}


def rewrite(policy, kind, edit):
    from pathlib import Path
    path = Path(policy[kind + "_path"])
    document = json.loads(path.read_text())
    edit(document)
    path.write_text(json.dumps(document))
    policy[kind + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def wrapper(policy, completion, **kwargs):
    return OpenRouterFreeLLM(SimpleNamespace(_temperature=None, _reasoning_effort=None), MODEL, policy,
                             completion=completion, **kwargs)


@pytest.mark.parametrize("tag", ["recursive/bf16", "baseten/fp4/us/priority"])
def test_endpoint_variant_preserves_exact_route_and_requires_base_provider_byok(policy, tag):
    rewrite(policy, "endpoints", lambda d: d["data"]["endpoints"][0].update(tag=tag))
    rewrite(policy, "account_evidence", lambda d: d.update(
        byok_providers={tag.split("/", 1)[0]: "Not configured"}))
    evidence = verify_free_evidence(MODEL, policy)
    assert evidence["provider_tags"] == [tag]


@pytest.mark.parametrize("byok", [
    {"recursive/bf16": "Not configured"},
    {"recursive": "Configured", "recursive/bf16": "Not configured"},
    {},
    None,
])
def test_endpoint_variant_never_infers_base_provider_byok_absence(policy, byok):
    rewrite(policy, "endpoints", lambda d: d["data"]["endpoints"][0].update(tag="recursive/bf16"))
    rewrite(policy, "account_evidence", lambda d: d.update(byok_providers=byok))
    with pytest.raises(FreeOnlyRoutingError, match="BYOK"):
        verify_free_evidence(MODEL, policy)


@pytest.mark.parametrize("tag", [
    None, "", "/recursive", "recursive/", "recursive//bf16", " recursive/bf16", "recursive/bf16 ",
    "https://recursive/bf16", "recursive/../bf16", "recursive/bf16?x=1", "recursive/bf16\n",
    "a/b/c/d/e", "recursive/" + "x" * 65, "recursiv\u0435/bf16",
])
def test_malformed_endpoint_provider_tag_fails_closed(policy, tag):
    rewrite(policy, "endpoints", lambda d: d["data"]["endpoints"][0].update(tag=tag))
    with pytest.raises(FreeOnlyRoutingError, match="provider tag"):
        verify_free_evidence(MODEL, policy)


def test_recorded_request_has_all_free_guards_and_provider_actual_zero(policy):
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        return response()
    budget = CallBudget(100000, 10000, 0, 0, 0)
    llm = BudgetedLLM(wrapper(policy, complete), budget, 1000)
    result = asyncio.run(llm.call("Task", response_format={"type": "object"}))
    assert result.usage.cost_usd == 0 and result.usage.prompt_tokens == 20
    request = calls[0]
    assert request["model"] == MODEL and request["api_base"] == "https://openrouter.ai/api/v1"
    assert request["extra_body"]["provider"] == {"only": ["recording"], "allow_fallbacks": False,
        "require_parameters": True, "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}}
    assert all(plugin["enabled"] is False for plugin in request["extra_body"]["plugins"])
    assert request["extra_headers"] == {"X-OpenRouter-Cache": "false"}
    assert request["drop_params"] is False and request["num_retries"] == request["max_retries"] == 0
    assert request["fallbacks"] == [] and "context_window_fallbacks" not in request
    assert "response_format" not in request and "JSON schema" in request["messages"][-1]["content"]
    assert "tools" not in request and "plugins" not in request
    assert budget.input_reserved > len(request["messages"][-1]["content"].encode())


@pytest.mark.parametrize("kind", ["catalog", "endpoints"])
@pytest.mark.parametrize("dimension", ["prompt", "completion", "request", "image", "input_cache_read", "new_fee"])
def test_any_billing_dimension_nonzero_blocks_before_dispatch(policy, kind, dimension):
    def edit(document):
        row = document["data"][0] if kind == "catalog" else document["data"]["endpoints"][0]
        row["pricing"][dimension] = "0.0000000000001"
    rewrite(policy, kind, edit)
    with pytest.raises(FreeOnlyRoutingError, match="billing dimension"):
        verify_free_evidence(MODEL, policy)


def test_evidence_hash_timestamp_endpoint_status_and_account_safety(policy):
    stale = {**policy, "verified_at_utc": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}
    with pytest.raises(FreeOnlyRoutingError, match="timestamp"):
        verify_free_evidence(MODEL, stale)
    with pytest.raises(FreeOnlyRoutingError, match="hash"):
        verify_free_evidence(MODEL, {**policy, "catalog_sha256": "0" * 64})
    rewrite(policy, "endpoints", lambda d: d["data"]["endpoints"][0].update(status=-5))
    with pytest.raises(FreeOnlyRoutingError, match="unavailable"):
        verify_free_evidence(MODEL, policy)
    rewrite(policy, "endpoints", lambda d: d["data"]["endpoints"][0].update(status=0))
    rewrite(policy, "account_evidence", lambda d: d.update(byok_present=True))
    with pytest.raises(FreeOnlyRoutingError, match="BYOK"):
        verify_free_evidence(MODEL, policy)


@pytest.mark.parametrize("cost", [None, True, "0", 1e-13, 1e-100, -0.1, float("nan"), float("inf")])
def test_missing_non_numeric_or_any_nonzero_provider_cost_stops_future_calls(policy, cost):
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        return response(cost=cost)
    budget = CallBudget(100000, 10000, 0, 0, 0)
    llm = BudgetedLLM(wrapper(policy, complete), budget, 1000)
    with pytest.raises(ProviderUsageError):
        asyncio.run(llm.call("Task"))
    with pytest.raises(ProviderUsageError):
        asyncio.run(llm.call("No second request"))
    assert len(calls) == 1 and budget.usage_uncertain


@pytest.mark.parametrize("field,value", [("prompt_tokens", None), ("prompt_tokens", True), ("completion_tokens", -1)])
def test_unknown_provider_tokens_are_never_invented(policy, field, value):
    async def complete(**kwargs):
        return response(**{field: value})
    with pytest.raises(ProviderUsageError):
        asyncio.run(BudgetedLLM(wrapper(policy, complete), CallBudget(100000, 10000, 0, 0, 0), 1000).call("Task"))


def test_every_request_rechecks_catalog_and_request_ceiling(policy):
    ticks = [0.0]
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
        ticks[0] += seconds
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        return response()
    free = wrapper(policy, complete, clock=lambda: ticks[0], sleep=sleep)
    async def run():
        await free.call("First", max_tokens=1000)
        await free.call("Second", max_tokens=1000)
        with pytest.raises(FreeOnlyRoutingError, match="ceiling"):
            await free.call("Third", max_tokens=1000)
    asyncio.run(run())
    assert len(calls) == 2 and sleeps == [4.0]


def test_positive_tiny_charge_not_tolerated_by_zero_call_budget():
    class Inner:
        async def call(self, prompt, **kwargs):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1, cost_usd=1e-13))
    with pytest.raises(ProviderUsageError, match="exceeded"):
        asyncio.run(BudgetedLLM(Inner(), CallBudget(100000, 1000, 0, 0, 0), 1000).call("Task"))


def test_changed_prices_are_rechecked_before_the_next_request(policy):
    calls = []
    async def complete(**kwargs):
        calls.append(kwargs)
        return response()
    async def no_delay(seconds):
        return None
    free = wrapper(policy, complete, sleep=no_delay)
    async def run():
        await free.call("First", max_tokens=1000)
        rewrite(policy, "catalog", lambda d: d["data"][0]["pricing"].update(request="0.01"))
        with pytest.raises(FreeOnlyRoutingError, match="billing dimension"):
            await free.call("Blocked", max_tokens=1000)
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("model", ["recording/other:free", "recording/terminal:paid", None])
def test_provider_cannot_substitute_an_unverified_model(policy, model):
    async def complete(**kwargs):
        data = response()
        data["model"] = model
        return data
    with pytest.raises(FreeOnlyRoutingError, match="model identity"):
        asyncio.run(BudgetedLLM(wrapper(policy, complete), CallBudget(100000, 10000, 0, 0, 0), 1000).call("Task"))


@pytest.mark.parametrize("field", ["default_enabled", "prevent_overrides"])
def test_omitting_unsupported_alignment_requires_verified_account_off(policy, field):
    rewrite(policy, "account_evidence", lambda d: d["plugins"]["alignment"].update({field: True}))
    with pytest.raises(FreeOnlyRoutingError, match="Alignment omission"):
        verify_free_evidence(MODEL, policy)


def test_zero_cost_agent_cannot_enter_standard_transport(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    pytest.importorskip("harbor")
    from harborforge.adapters.budgeted_agent import BudgetedTerminus2
    budget = {"max_input_tokens": 10000, "max_output_tokens": 1000, "max_cost_usd": 0,
              "input_usd_per_million": 0, "output_usd_per_million": 0}
    with pytest.raises(ValueError, match="verified free-only routing"):
        BudgetedTerminus2(campaign_budget=budget, model_name=MODEL)


def test_actual_terminus_initializes_unknown_free_model_from_verified_metadata(policy, tmp_path, monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import socket
    def forbid_network(*args, **kwargs):
        raise AssertionError("Constructor/getters must not make network requests")
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    import litellm
    from harborforge.adapters.budgeted_agent import BudgetedTerminus2
    from harborforge.adapters.openrouter_free import trusted_free_model_info
    monkeypatch.delitem(litellm.model_cost, MODEL, raising=False)
    assert MODEL not in litellm.model_cost
    async def forbid_provider(**kwargs):
        raise AssertionError("Constructor must not call a model")
    monkeypatch.setattr(litellm, "acompletion", forbid_provider)
    logs = tmp_path / "agent"
    logs.mkdir()
    budget = {"max_input_tokens": 10000, "max_output_tokens": 1000, "max_cost_usd": 0,
              "input_usd_per_million": 0, "output_usd_per_million": 0}
    agent = BudgetedTerminus2(logs_dir=logs, model_name=MODEL, campaign_budget=budget,
        free_routing=policy, model_info=trusted_free_model_info(MODEL, policy))
    assert isinstance(agent._llm, BudgetedLLM)
    assert isinstance(agent._llm.inner, OpenRouterFreeLLM)
    assert agent._llm.get_model_context_limit() == 100000
    assert agent._llm.get_model_output_limit() == 8000
    assert agent._llm.inner.inner.get_model_context_limit() == 100000
    assert agent._llm.inner.inner.get_model_output_limit() == 8000
    assert agent._llm.inner.requests == 0


def test_reasoning_only_length_retains_authoritative_zero_usage_before_text_validation(policy):
    from harbor.llms.base import OutputLengthExceededError
    async def complete(**kwargs):
        data = response(prompt_tokens=2093, completion_tokens=2048)
        data["id"] = "gen-recorded-reasoning-limit"
        data["choices"][0].update(finish_reason="length", message={"role": "assistant", "content": None})
        return data
    free = wrapper(policy, complete)
    # This direct fixture invokes the same guard with an explicit local envelope;
    # outer known-error accounting is covered by test_free_response_accounting.
    with pytest.raises(OutputLengthExceededError) as caught:
        asyncio.run(free.call("Task", max_tokens=2048))
    assert caught.value.usage_verified is True and caught.value.usage.cost_usd == 0
    assert caught.value.usage.completion_tokens == 2048 and caught.value.truncated_response == ""
    assert len(free.audit) == 1 and free.audit[0]["provider_cost_usd"] == 0
    assert free.last_failed_response_meta == {"provider_request_id": "gen-recorded-reasoning-limit", "model": SLUG,
        "provider_cost_usd": 0, "prompt_tokens": 2093, "completion_tokens": 2048,
        "finish_reason": "length", "usage_verified": True}


def test_nonlength_null_content_is_invalid_response_with_known_usage(policy):
    from harborforge.adapters.call_budget import ProviderResponseError
    async def complete(**kwargs):
        data = response()
        data["choices"][0]["message"]["content"] = None
        return data
    free = wrapper(policy, complete)
    with pytest.raises(ProviderResponseError) as caught:
        asyncio.run(free.call("Task", max_tokens=2048))
    assert caught.value.usage_verified is True and caught.value.usage.prompt_tokens == 20
    assert len(free.audit) == 1 and free.last_failed_response_meta["finish_reason"] == "stop"


def test_unverified_response_cost_never_creates_a_verified_receipt(policy):
    async def complete(**kwargs):
        return response(cost=None)
    free = wrapper(policy, complete)
    with pytest.raises(ProviderUsageError):
        asyncio.run(free.call("Task", max_tokens=2048))
    assert free.audit == [] and free.last_failed_response_meta["usage_verified"] is False
