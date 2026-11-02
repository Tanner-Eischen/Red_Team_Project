"""Host-only, evidence-bound OpenRouter text requests with an exact $0 ceiling.

This wrapper deliberately does not call Harbor LiteLLM.call: that pinned method
can retry after deleting extra_body and estimates costs when reported cost is 0.
The existing LiteLLM SDK remains the transport, inside BudgetedLLM reservations.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

from .call_budget import ACTIVE_REQUEST_RESERVATION, AgentBudgetExceededError, ProviderResponseError, ProviderUsageError

API_BASE = "https://openrouter.ai/api/v1"
MODEL_PATTERN = re.compile(r"openrouter/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:free\Z")
DISABLED_PLUGINS = {"web", "file-parser", "response-healing", "armor", "pareto-router", "context-compression"}


class FreeOnlyRoutingError(ProviderUsageError):
    """No further dispatch is allowed until free-only evidence is reconciled."""


def _zero(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return False
    try:
        amount = Decimal(str(value))
        return amount.is_finite() and amount == 0
    except InvalidOperation:
        return False


def _fresh(value: str, maximum_age: int, now: datetime) -> None:
    try:
        checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
        age = (now - checked).total_seconds()
        if checked.tzinfo is None or not 0 <= age <= maximum_age:
            raise ValueError("stale or future evidence")
    except (AttributeError, TypeError, ValueError):
        raise FreeOnlyRoutingError("Free-only evidence timestamp is stale or invalid") from None


def _read_evidence(path: str, expected: str) -> dict:
    target = Path(path)
    if target.is_symlink() or not target.is_file() or target.stat().st_size > 16 * 1024 * 1024:
        raise FreeOnlyRoutingError("Free-only evidence is not a bounded regular file")
    payload = target.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise FreeOnlyRoutingError("Free-only evidence hash changed")
    try:
        value = json.loads(payload)
    except (UnicodeError, ValueError):
        raise FreeOnlyRoutingError("Free-only evidence is not valid JSON") from None
    if not isinstance(value, dict):
        raise FreeOnlyRoutingError("Free-only evidence requires an object")
    return value


def _pricing(value: Any) -> None:
    if not isinstance(value, dict) or not {"prompt", "completion"} <= set(value):
        raise FreeOnlyRoutingError("Missing explicit prompt/completion prices")
    if any(not _zero(price) for price in value.values()):
        raise FreeOnlyRoutingError("A billing dimension is nonzero or unknown")


def verify_free_evidence(model_id: str, policy: dict, *, now: datetime | None = None) -> dict:
    """Reread catalog, endpoint and account snapshots before every dispatch."""
    now = now or datetime.now(timezone.utc)
    if not MODEL_PATTERN.fullmatch(model_id) or model_id not in policy.get("allowed_model_ids", []):
        raise FreeOnlyRoutingError("Only explicitly allowed named :free models may run")
    if (policy.get("policy_version") != "openrouter-free-only-v1"
            or policy.get("allow_fallbacks") is not False or policy.get("allow_paid_fallback") is not False
            or not _zero(policy.get("max_usd_per_request"))):
        raise FreeOnlyRoutingError("Free-only routing policy is incomplete")
    caps = policy.get("provider_max_price")
    if not isinstance(caps, dict) or set(caps) != {"prompt", "completion", "request", "image"} or not all(_zero(v) for v in caps.values()):
        raise FreeOnlyRoutingError("Every supported routing price ceiling must be zero")
    age = policy.get("max_age_seconds")
    if type(age) is not int or not 60 <= age <= 3600:
        raise FreeOnlyRoutingError("Invalid free-only evidence age bound")
    _fresh(policy.get("verified_at_utc"), age, now)
    catalog = _read_evidence(policy["catalog_path"], policy["catalog_sha256"])
    endpoints = _read_evidence(policy["endpoints_path"], policy["endpoints_sha256"])
    account = _read_evidence(policy["account_evidence_path"], policy["account_evidence_sha256"])
    _fresh(account.get("verified_at_utc"), age, now)
    if (account.get("byok_present") is not False or account.get("paid_plugins_enabled") is not False
            or account.get("byok_only_routing") is not False or account.get("forced_paid_plugins") is not False
            or account.get("default_plugin_overrides_locked") is not False or account.get("server_tools_requested") != []):
        raise FreeOnlyRoutingError("Account BYOK/plugin billing has not been excluded")
    disabled = account.get("plugins_disabled")
    if (not isinstance(disabled, list) or not disabled or len(disabled) != len(set(disabled))
            or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name) for name in disabled)
            or set(disabled) != DISABLED_PLUGINS):
        raise FreeOnlyRoutingError("Explicit disabled plugin IDs are required")
    # AlignmentPlugin has no 'enabled' field and requires nonempty rules; there
    # is no schema-valid disabled object. Omission requires account evidence.
    alignment = account.get("plugins", {}).get("alignment", {})
    if (alignment.get("default_enabled") is not False or alignment.get("prevent_overrides") is not False
            or not isinstance(alignment.get("source"), str) or not alignment["source"].strip()):
        raise FreeOnlyRoutingError("Alignment omission requires verified disabled/unforced account defaults")
    slug = model_id.removeprefix("openrouter/")
    rows = [row for row in catalog.get("data", []) if row.get("id") == slug]
    if len(rows) != 1:
        raise FreeOnlyRoutingError("Exact free model is absent or ambiguous in catalog")
    model = rows[0]
    _pricing(model.get("pricing"))
    data = endpoints.get("data", {})
    if data.get("id") != slug:
        raise FreeOnlyRoutingError("Endpoint evidence belongs to another model")
    routes = data.get("endpoints")
    if not isinstance(routes, list) or not routes:
        raise FreeOnlyRoutingError("No explicitly priced free endpoint")
    tags = []
    supported = set(model.get("supported_parameters") or [])
    for route in routes:
        if route.get("model_id") != slug or type(route.get("status")) is not int or route["status"] != 0:
            raise FreeOnlyRoutingError("Endpoint is unavailable or is another model")
        _pricing(route.get("pricing"))
        tag = route.get("tag")
        if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", tag):
            raise FreeOnlyRoutingError("Endpoint has no explicit provider tag")
        tags.append(tag)
        supported &= set(route.get("supported_parameters") or [])
    if any(account.get("byok_providers", {}).get(tag) != "Not configured" for tag in tags):
        raise FreeOnlyRoutingError("Selected provider BYOK status is not verified absent")
    canonical = model.get("canonical_slug")
    known = {slug, slug.removesuffix(":free")}
    if isinstance(canonical, str) and canonical.startswith(slug.split("/", 1)[0] + "/"):
        known.update({canonical, canonical + ":free"})
    aliases = set(policy.get("response_model_ids") or [])
    if not aliases or not aliases <= known:
        raise FreeOnlyRoutingError("Unverified response-model alias")
    return {"model": model, "routes": routes, "provider_tags": sorted(set(tags)),
            "supported_parameters": supported, "response_model_ids": aliases, "disabled_plugins": disabled}


def trusted_free_model_info(model_id: str, policy: dict) -> dict:
    """Register absent SDK metadata only from validated zero-price snapshots."""
    evidence = verify_free_evidence(model_id, policy)
    contexts = [evidence["model"].get("context_length")]
    outputs = []
    for route in evidence["routes"]:
        contexts.append(route.get("context_length"))
        if route.get("max_prompt_tokens") is not None:
            contexts.append(route["max_prompt_tokens"])
        outputs.append(route.get("max_completion_tokens"))
    if any(type(value) is not int or value <= 0 for value in [*contexts, *outputs]):
        raise FreeOnlyRoutingError("Trusted context/output metadata is unavailable")
    return {"max_tokens": min(contexts), "max_input_tokens": min(contexts), "max_output_tokens": min(outputs),
            "input_cost_per_token": 0.0, "output_cost_per_token": 0.0,
            "litellm_provider": "openrouter", "mode": "chat",
            "supports_function_calling": "tools" in evidence["supported_parameters"],
            "supports_response_schema": False}


class OpenRouterFreeLLM:
    """One text-only LiteLLM request per accounted call, with no SDK retries."""

    def __init__(self, inner: Any, model_id: str, policy: dict, *, completion=None, clock=None, sleep=None,
                 http_transport=None):
        self.inner, self.model_id, self.policy = inner, model_id, policy
        self._completion = completion
        self._http_transport = http_transport
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self.requests = 0
        self.audit: list[dict] = []
        self.last_failed_response_meta: dict | None = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def get_model_context_limit(self):
        return trusted_free_model_info(self.model_id, self.policy)["max_input_tokens"]

    def get_model_output_limit(self):
        return trusted_free_model_info(self.model_id, self.policy)["max_output_tokens"]

    def prepare_budgeted_call(self, prompt: str, kwargs: dict) -> tuple[str, dict]:
        # This known resource stop precedes BudgetedLLM's reservation and its
        # uncertain-dispatch exception boundary. No new request has been sent.
        cap = self.policy.get("max_requests_per_attempt", 12)
        if type(cap) is not int or not 1 <= cap <= 16:
            raise FreeOnlyRoutingError("Free-only per-attempt request ceiling is invalid")
        if self.requests >= cap:
            raise AgentBudgetExceededError("Free-only per-attempt request ceiling reached before dispatch")
        # Match the same prompt-only schema mode across every model. Crucially,
        # BudgetedLLM reserves this actual formatted text, not a smaller prompt.
        kwargs = dict(kwargs)
        schema = kwargs.pop("response_format", None)
        if schema is not None:
            value = schema if isinstance(schema, dict) else schema.model_json_schema()
            prompt = "Respond using this JSON schema:\n" + json.dumps(value, sort_keys=True) + "\n\n" + prompt
        kwargs.pop("previous_response_id", None)
        temperature = getattr(self.inner, "_temperature", None)
        if temperature is not None:
            kwargs.setdefault("temperature", temperature)
        return prompt, kwargs

    async def _sdk_request(self, request: dict, evidence: dict):
        """Check the real serialized HTTP request after all SDK transformations."""
        import litellm
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
        if any(getattr(litellm, key, None) for key in ("fallbacks", "context_window_fallbacks", "model_alias_map")):
            raise FreeOnlyRoutingError("Global SDK fallback/alias routing is prohibited")
        expected_body = deepcopy(request["extra_body"])
        expected_messages = deepcopy(request["messages"])
        expected_sampling = {key: request.get(key) for key in ("temperature", "seed", "top_p")}
        sent = 0
        async def before_send(http_request):
            nonlocal sent
            verify_free_evidence(self.model_id, self.policy)
            if sent or str(http_request.url) != API_BASE + "/chat/completions":
                raise FreeOnlyRoutingError("An SDK retry, redirect or different origin is prohibited")
            reservation = ACTIVE_REQUEST_RESERVATION.get()
            if reservation is None or len(http_request.content) > reservation["input"]:
                raise FreeOnlyRoutingError("Serialized request has no sufficient token reservation")
            value = json.loads(http_request.content)
            allowed = {"model", "messages", "max_tokens", "temperature", "seed", "top_p", "stream", "n",
                       "provider", "plugins", "usage", "reasoning"}
            checks = {"unknown_fields": not (set(value) - allowed),
                "model": value.get("model") == self.model_id.removeprefix("openrouter/"),
                "messages": value.get("messages") == expected_messages,
                "max_tokens": value.get("max_tokens") == reservation["output"],
                "provider": value.get("provider") == expected_body["provider"],
                "plugins": value.get("plugins") == expected_body["plugins"],
                "usage": value.get("usage") == {"include": True},
                "stream": value.get("stream", False) is False, "n": value.get("n", 1) == 1,
                "reasoning": value.get("reasoning") == expected_body.get("reasoning"),
                "cache_header": http_request.headers.get("x-openrouter-cache") == "false"}
            if not all(checks.values()):
                details = ",".join(k for k, ok in checks.items() if not ok)
                if set(value) - allowed:
                    details += " (" + ",".join(sorted(set(value) - allowed)) + ")"
                raise FreeOnlyRoutingError("SDK changed free-only HTTP fields: " + details)
            for key in ("temperature", "seed", "top_p"):
                if value.get(key) != expected_sampling[key]:
                    raise FreeOnlyRoutingError("SDK changed a matched sampling parameter")
            sent += 1
        client = AsyncHTTPHandler(timeout=request["timeout"], event_hooks={"request": [before_send]},
                                  transport=self._http_transport, follow_redirects=False, ssl_verify=True)
        try:
            response = await litellm.acompletion(**request, client=client)
            if sent != 1:
                raise FreeOnlyRoutingError("SDK returned without one validated fresh HTTP request")
            return response
        finally:
            await client.client.aclose()

    async def call(self, prompt: str, **kwargs):
        async with self._lock:
            cap = self.policy.get("max_requests_per_attempt", 12)
            interval = self.policy.get("minimum_request_interval_seconds", 4)
            if type(cap) is not int or not 1 <= cap <= 16 or self.requests >= cap:
                raise FreeOnlyRoutingError("Free-only per-attempt request ceiling reached")
            if (not isinstance(interval, (int, float)) or isinstance(interval, bool)
                    or not math.isfinite(interval) or interval < 4):
                raise FreeOnlyRoutingError("Free-only request spacing must be at least four seconds")
            if self._last_request is not None:
                delay = interval - (self._clock() - self._last_request)
                if delay > 0:
                    await self._sleep(delay)
            evidence = verify_free_evidence(self.model_id, self.policy)
            allowed = {"message_history", "logging_path", "max_tokens", "num_retries", "temperature", "seed", "top_p", "timeout"}
            if set(kwargs) - allowed:
                raise FreeOnlyRoutingError("Unsupported free-only request option")
            history = kwargs.get("message_history") or []
            messages = [*history, {"role": "user", "content": prompt}]
            if any(not isinstance(message, dict) or set(message) - {"role", "content"}
                   or message.get("role") not in {"system", "user", "assistant"}
                   or not isinstance(message.get("content"), str) for message in messages):
                raise FreeOnlyRoutingError("Only plain text messages are allowed")
            settings = {key: kwargs[key] for key in ("max_tokens", "temperature", "seed", "top_p") if key in kwargs}
            if not set(settings) <= evidence["supported_parameters"]:
                raise FreeOnlyRoutingError("A requested parameter is unsupported by a free endpoint")
            if type(settings.get("max_tokens")) is not int or settings["max_tokens"] <= 0:
                raise FreeOnlyRoutingError("A bounded output allowance is required")
            context_upper = len(json.dumps(messages, ensure_ascii=False).encode()) + 4096 + settings["max_tokens"]
            if context_upper > evidence["model"]["context_length"]:
                raise FreeOnlyRoutingError("Conservative request envelope exceeds catalog context")
            for route in evidence["routes"]:
                if context_upper > route.get("context_length", 0):
                    raise FreeOnlyRoutingError("Request envelope exceeds endpoint context")
                limit = route.get("max_completion_tokens")
                if limit is not None and settings["max_tokens"] > limit:
                    raise FreeOnlyRoutingError("Output allowance exceeds endpoint limit")
            body = {"provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
                                 "only": evidence["provider_tags"], "allow_fallbacks": False, "require_parameters": True},
                    "plugins": [{"id": name, "enabled": False} for name in evidence["disabled_plugins"]],
                    "usage": {"include": True}}
            reasoning = getattr(self.inner, "_reasoning_effort", None)
            if reasoning is not None:
                if reasoning != "low" or "reasoning" not in evidence["supported_parameters"]:
                    raise FreeOnlyRoutingError("Common low reasoning setting is unsupported")
                body["reasoning"] = {"effort": "low"}
            request = {"model": self.model_id, "messages": messages, **settings, "extra_body": body,
                       "api_base": API_BASE, "extra_headers": {"X-OpenRouter-Cache": "false"},
                       "drop_params": False, "num_retries": 0, "max_retries": 0,
                       "fallbacks": [], "stream": False,
                       "timeout": min(float(kwargs.get("timeout", 120)), 120)}
            self.requests += 1
            self._last_request = self._clock()
            if self._completion is None:
                response = await self._sdk_request(request, evidence)
            else:
                response = await self._completion(**request)
            hidden = getattr(response, "_hidden_params", {})
            response = response if isinstance(response, dict) else response.model_dump()
            usage = response.get("usage")
            value = usage if isinstance(usage, dict) else (usage.model_dump() if hasattr(usage, "model_dump") else {})
            cost = value.get("cost")
            tokens_in, tokens_out = value.get("prompt_tokens"), value.get("completion_tokens")
            choices = response.get("choices")
            first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            self.last_failed_response_meta = {"provider_request_id": response.get("id") if isinstance(response.get("id"), str) else None,
                "model": response.get("model") if isinstance(response.get("model"), str) else None,
                "provider_cost_usd": cost if type(cost) in (int, float) and math.isfinite(cost) else None,
                "prompt_tokens": tokens_in if type(tokens_in) is int else None,
                "completion_tokens": tokens_out if type(tokens_out) is int else None,
                "finish_reason": first.get("finish_reason") if isinstance(first.get("finish_reason"), str) else None,
                "usage_verified": False}
            headers = hidden.get("additional_headers", {}) if isinstance(hidden, dict) else {}
            if any("cache" in str(key).lower() and "hit" in str(value).lower()
                   for key, value in headers.items()):
                raise FreeOnlyRoutingError("A cached response cannot serve as a fresh pilot attempt")
            if not _zero(cost) or isinstance(cost, str):
                raise FreeOnlyRoutingError("Provider did not report an exact numeric zero actual cost")
            if type(tokens_in) is not int or tokens_in < 0 or type(tokens_out) is not int or tokens_out < 0:
                raise FreeOnlyRoutingError("Provider token usage is missing or invalid")
            returned_model = response.get("model")
            if returned_model not in evidence["response_model_ids"]:
                raise FreeOnlyRoutingError("Provider returned an unverified model identity")
            reservation = ACTIVE_REQUEST_RESERVATION.get()
            input_bound = reservation["input"] if reservation is not None else context_upper
            output_bound = reservation["output"] if reservation is not None else settings["max_tokens"]
            if tokens_in > input_bound or tokens_out > output_bound:
                raise FreeOnlyRoutingError("Provider usage exceeded this request's reserved envelope")
            from harbor.llms.base import LLMResponse, OutputLengthExceededError
            from harbor.models.metric import UsageInfo
            observed = UsageInfo(prompt_tokens=tokens_in, completion_tokens=tokens_out, cache_tokens=0, cost_usd=0.0)
            self.last_failed_response_meta["usage_verified"] = True
            self.audit.append({"request": self.requests, "model": returned_model, "provider_cost_usd": 0,
                               "prompt_tokens": tokens_in, "completion_tokens": tokens_out,
                               "provider_request_id": response.get("id"), "pricing_evidence_verified": True,
                               "finish_reason": first.get("finish_reason")})
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0].get("message"), dict):
                raise ProviderResponseError("Malformed nonstreaming provider result", usage=observed, usage_verified=True)
            message = choices[0]["message"]
            if message.get("tool_calls") or message.get("function_call"):
                raise ProviderResponseError("Paid/native tool execution is disabled", usage=observed, usage_verified=True)
            content = message.get("content")
            logging_path = kwargs.get("logging_path")
            if logging_path is not None:
                from .harbor_adapter import redact
                path = Path(logging_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(redact(json.dumps({"request": {"model": self.model_id, "messages": messages,
                    "max_tokens": settings["max_tokens"], "extra_body": body},
                    "response": {"id": response.get("id"), "model": returned_model, "content": content},
                    "provider_usage": self.audit[-1]}, indent=2)), encoding="utf-8")
            if choices[0].get("finish_reason") == "length":
                error = OutputLengthExceededError("Free-only response reached its output limit",
                    truncated_response=content if isinstance(content, str) else "")
                error.usage, error.usage_verified = observed, True
                raise error
            if not isinstance(content, str):
                raise ProviderResponseError("Provider did not return text", usage=observed, usage_verified=True)
            self.last_failed_response_meta = None
            return LLMResponse(content=content, model_name=returned_model,
                reasoning_content=message.get("reasoning_content"),
                usage=observed)
