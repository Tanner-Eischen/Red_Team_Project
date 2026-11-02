"""Real pinned LiteLLM serialization through an in-memory HTTP transport only."""
from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from test_openrouter_free import MODEL, SLUG
from test_openrouter_free import policy as policy_fixture
from harborforge.adapters.call_budget import BudgetedLLM, CallBudget
from harborforge.adapters.openrouter_free import OpenRouterFreeLLM


@pytest.fixture
def policy(tmp_path):
    return policy_fixture.__wrapped__(tmp_path)


def sdk_setup(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    connect = socket.socket.connect
    address_info = socket.getaddrinfo
    def no_network(self, address):
        # Windows asyncio creates its internal self-pipe using loopback TCP.
        if self.family == getattr(socket, "AF_UNIX", -1) or (isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}):
            return connect(self, address)
        raise AssertionError("Recording test attempted a real network connection")
    def local_address_info(host, *args, **kwargs):
        if host in {"127.0.0.1", "::1", "localhost"}:
            return address_info(host, *args, **kwargs)
        raise AssertionError("Recording test blocked external DNS: " + str(host))
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", local_address_info)
    litellm = pytest.importorskip("litellm")
    # Tokenizer blob downloads are unrelated to this transport test. Provider
    # token counts below are explicit recorded response data, never estimated.
    main = importlib.import_module("litellm.main")
    monkeypatch.setattr(main, "_get_encoding", lambda: SimpleNamespace(
        encode=lambda value, **kwargs: [0] * len(value), decode=lambda value: "x" * len(value)))
    monkeypatch.setattr(litellm, "completion_cost", lambda *args, **kwargs: 999.0)
    original = litellm.acompletion
    async def with_recording_key(**kwargs):
        return await original(**kwargs, api_key="recording-only-not-a-credential")
    monkeypatch.setattr(litellm, "acompletion", with_recording_key)
    return litellm


@pytest.mark.parametrize("reasoning", [None, "low"])
def test_actual_sdk_http_body_preserves_zero_guards_and_raw_provider_cost(monkeypatch, policy, reasoning):
    import httpx
    sdk_setup(monkeypatch)
    seen = []
    async def transport(request):
        from jsonschema import Draft202012Validator
        plugin_schema = json.loads((Path(__file__).parent / "fixtures/openrouter_plugins.schema.json").read_text())
        for plugin in json.loads(request.content)["plugins"]:
            Draft202012Validator(plugin_schema).validate(plugin)
        seen.append({"url": str(request.url), "body": json.loads(request.content),
                     "cache_header": request.headers.get("x-openrouter-cache")})
        return httpx.Response(200, json={"id": "recorded-http", "object": "chat.completion", "created": 1,
            "model": SLUG, "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": 4,
                    "total_tokens": 54, "cost": 0}})
    free = OpenRouterFreeLLM(SimpleNamespace(_temperature=0, _reasoning_effort=reasoning), MODEL, policy,
                             http_transport=httpx.MockTransport(transport))
    result = asyncio.run(BudgetedLLM(free, CallBudget(100000, 10000, 0, 0, 0), 1000).call("Task"))
    assert len(seen) == 1 and result.usage.cost_usd == 0 and result.usage.prompt_tokens == 50
    request = seen[0]
    assert request["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert request["body"]["model"] == SLUG and request["cache_header"] == "false"
    assert request["body"]["provider"] == {"only": ["recording"], "allow_fallbacks": False,
        "require_parameters": True, "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}}
    assert all(plugin["enabled"] is False for plugin in request["body"]["plugins"])
    assert len(request["body"]["plugins"]) == 6 and not any(p["id"] == "alignment" for p in request["body"]["plugins"])
    assert request["body"]["usage"] == {"include": True}
    assert request["body"].get("reasoning") == ({"effort": "low"} if reasoning else None)
    assert not {"models", "tools", "preset", "response_format", "web_search_options"} & request["body"].keys()


@pytest.mark.parametrize("status", [400, 429, 500])
def test_sdk_provider_errors_make_one_http_attempt_without_guard_removal(monkeypatch, policy, status):
    import httpx
    sdk_setup(monkeypatch)
    seen = []
    async def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(status, json={"error": {"message": "Unrecognized request argument provider", "code": status}})
    free = OpenRouterFreeLLM(SimpleNamespace(_temperature=None, _reasoning_effort=None), MODEL, policy,
                             http_transport=httpx.MockTransport(transport))
    budget = CallBudget(100000, 10000, 0, 0, 0)
    with pytest.raises(Exception):
        asyncio.run(BudgetedLLM(free, budget, 1000).call("Task"))
    assert len(seen) == 1 and seen[0]["provider"]["max_price"]["prompt"] == 0
    assert budget.usage_uncertain


def test_sdk_provider_transform_override_is_blocked_before_http(monkeypatch, policy):
    import httpx
    litellm = sdk_setup(monkeypatch)
    original_transform = litellm.OpenrouterConfig.transform_request
    def changed(self, *args, **kwargs):
        body = original_transform(self, *args, **kwargs)
        # Deliberate in-place mutation also tests frozen expected guard values.
        body["provider"]["allow_fallbacks"] = True
        return body
    monkeypatch.setattr(litellm.OpenrouterConfig, "transform_request", changed)
    seen = []
    async def transport(request):
        seen.append(request)
        return httpx.Response(500)
    free = OpenRouterFreeLLM(SimpleNamespace(_temperature=None, _reasoning_effort=None), MODEL, policy,
                             http_transport=httpx.MockTransport(transport))
    with pytest.raises(Exception):
        asyncio.run(BudgetedLLM(free, CallBudget(100000, 10000, 0, 0, 0), 1000).call("Task"))
    assert seen == []


def test_current_official_schema_rejects_the_real_alignment_400_payload():
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).parent / "fixtures/openrouter_plugins.schema.json").read_text())
    validator = Draft202012Validator(schema)
    # Root's actual v1 availability request failed before model inference with
    # plugins.3.rules expected-array error. Neither empty nor dummy rules can
    # turn enabled:false into an accepted AlignmentPlugin schema property.
    assert not validator.is_valid({"id": "alignment", "enabled": False})
    assert not validator.is_valid({"id": "alignment", "enabled": False, "rules": []})
    assert not validator.is_valid({"id": "alignment", "enabled": False,
                                  "rules": [{"rule": "Disabled.", "watch": ["content"]}]})


def test_actual_sdk_reasoning_only_length_keeps_verified_zero_without_retry(monkeypatch, policy):
    import httpx
    from harbor.llms.base import OutputLengthExceededError
    sdk_setup(monkeypatch)
    seen = []
    async def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "gen-recording-output-limit", "object": "chat.completion", "created": 1,
            "model": SLUG, "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                "reasoning": "Reasoning consumed the declared allowance."}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 2093, "completion_tokens": 2048, "total_tokens": 4141, "cost": 0}})
    free = OpenRouterFreeLLM(SimpleNamespace(_temperature=0, _reasoning_effort=None), MODEL, policy,
                             http_transport=httpx.MockTransport(transport))
    budget = CallBudget(100000, 10000, 0, 0, 0)
    with pytest.raises(OutputLengthExceededError) as caught:
        asyncio.run(BudgetedLLM(free, budget, 2048).call("Task"))
    assert caught.value.usage_verified is True and caught.value.usage.cost_usd == 0
    assert len(seen) == 1 and not budget.usage_uncertain
    assert free.last_failed_response_meta["provider_request_id"] == "gen-recording-output-limit"
    assert free.audit[0]["completion_tokens"] == 2048
