"""LLM gateway: LiteLLM response cache + ordered key failover.

Request flow for one chat turn::

    messages ──► LiteLLM cache lookup (model + full message list)
                    │ hit  ──► LLM_CACHE  (stored reply + stored usage, key_id=None)
                    │ miss
                    ▼
                 for key in pool.attempt_order():          # key_1, key_2, ...
                    litellm.acompletion(api_key=key, num_retries=0)
                      ├─ ok      ──► store in LiteLLM cache ──► LIVE_INFERENCE
                      └─ failure ──► record, try next key   (429 / quota / 5xx / timeout / auth)
                 all keys failed ──► AllProvidersUnavailable  (HTTP 503)

Why the failover loop lives here instead of in ``litellm.Router``
-----------------------------------------------------------------
The contract needs, per request, *which* key answered (``key_id``), *how many*
keys failed first (``fallback_attempts``), and a way to make specific keys
fail for one request only (``X-Simulate-Failure``). Router fallbacks hide the
first two behind callbacks and its cooldowns are global, so a simulated 429
would bleed into later requests. Driving ``litellm.acompletion`` once per key
with ``num_retries=0`` keeps every LiteLLM benefit (provider abstraction,
exception normalisation, usage accounting, the built-in cache) while making
the failover deterministic and observable.

Simulated failures are injected *through LiteLLM*: we pass the provider-shaped
exception (``litellm.RateLimitError`` etc.) as ``mock_response``, so a
simulated 429 takes the same code path as a real one.

Why cache reads/writes are explicit
-----------------------------------
With ``caching=True`` LiteLLM writes the cache in a background task, so an
identical request arriving a few milliseconds later can still miss. We call
the same LiteLLM ``Cache`` object directly and *await* the write before
responding, which makes "send it twice, the second one is LLM_CACHE"
deterministic. The key is LiteLLM's own ``get_cache_key`` over
``model + messages`` (+ the fixed sampling params), so the API key never
influences it: a reply produced by key_2 is a hit for a request first routed
to key_1.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import litellm
from litellm.caching.caching import Cache

from ..config import Settings
from .key_pool import ApiKey, KeyPool
from .simulation import NO_FAILURES, FailurePlan

logger = logging.getLogger("lyftr.llm")

LIVE_INFERENCE = "LIVE_INFERENCE"
LLM_CACHE = "LLM_CACHE"

CompletionFn = Callable[..., Awaitable[Any]]

_SIMULATED_EXCEPTIONS: dict[int, type[Exception]] = {
    429: litellm.RateLimitError,
    500: litellm.InternalServerError,
    502: litellm.BadGatewayError,
    503: litellm.ServiceUnavailableError,
}


@dataclass
class Attempt:
    key_id: str
    model: str
    status: int | None
    error: str
    simulated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id,
            "model": self.model,
            "status": self.status,
            "error": self.error,
            "simulated": self.simulated,
        }


@dataclass
class CompletionResult:
    reply: str
    resolution_source: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int
    attempts: list[Attempt] = field(default_factory=list)


class AllProvidersUnavailable(Exception):
    def __init__(self, attempts: list[Attempt]) -> None:
        super().__init__("all providers unavailable")
        self.attempts = attempts


class EmptyCompletion(Exception):
    """The provider answered 200 but with no text (safety block, token cap...)."""


class LLMGateway:
    def __init__(self, settings: Settings, pool: KeyPool, completion_fn: CompletionFn | None = None) -> None:
        self.settings = settings
        self.pool = pool
        self._completion: CompletionFn = completion_fn or litellm.acompletion
        self._secrets = [k.secret for k in pool.keys]

        litellm.suppress_debug_info = True
        litellm.drop_params = True  # tolerate providers that don't accept every sampling param

        self.cache: Cache | None = None
        if settings.cache_enabled:
            if settings.cache_type == "disk":
                self.cache = Cache(type="disk", disk_cache_dir=settings.cache_dir, ttl=settings.cache_ttl_seconds)
            else:
                self.cache = Cache(type="local", ttl=settings.cache_ttl_seconds)
        # Expose it as LiteLLM's global cache too; calls below pass caching=False
        # and drive this object explicitly (see module docstring).
        litellm.cache = self.cache

    @property
    def cache_enabled(self) -> bool:
        return self.cache is not None

    # ------------------------------------------------------------------ cache
    def _sampling_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"max_tokens": self.settings.llm_max_output_tokens}
        if self.settings.llm_temperature is not None:
            params["temperature"] = self.settings.llm_temperature
        return params

    def _cache_kwargs(self, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        return {"model": model, "messages": messages, **self._sampling_params()}

    async def _cache_lookup(self, messages: list[dict[str, str]]) -> CompletionResult | None:
        if self.cache is None:
            return None
        for model in self.pool.distinct_models():
            try:
                cached = await self.cache.async_get_cache(**self._cache_kwargs(model, messages))
            except Exception:  # a broken cache must never break chat
                logger.exception("cache lookup failed")
                return None
            if not cached:
                continue
            if not isinstance(cached, dict):
                cached = cached.model_dump() if hasattr(cached, "model_dump") else dict(cached)
            reply = _extract_text_from_dict(cached)
            if not reply:
                continue
            usage = cached.get("usage") or {}
            return CompletionResult(
                reply=reply,
                resolution_source=LLM_CACHE,
                model=model,
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                key_id=None,
                fallback_attempts=0,
            )
        return None

    async def _cache_store(self, response: Any, model: str, messages: list[dict[str, str]]) -> None:
        if self.cache is None:
            return
        try:
            await self.cache.async_add_cache(response, **self._cache_kwargs(model, messages))
        except Exception:
            logger.exception("cache store failed")

    # --------------------------------------------------------------- failover
    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    def _simulated_exception(self, status: int, key: ApiKey) -> Exception:
        exc_type = _SIMULATED_EXCEPTIONS[status]
        provider = key.model.split("/", 1)[0] if "/" in key.model else "openai"
        return exc_type(
            message=f"simulated HTTP {status} via X-Simulate-Failure",
            llm_provider=provider,
            model=key.model,
        )

    async def _call_key(self, key: ApiKey, messages: list[dict[str, str]], simulate: int | None) -> Any:
        kwargs: dict[str, Any] = {
            "model": key.model,
            "messages": messages,
            "api_key": key.secret,
            "timeout": self.settings.llm_timeout_seconds,
            "num_retries": 0,  # failover across keys is our job, not a retry on the same key
            "caching": False,  # cache is driven explicitly, see module docstring
            **self._sampling_params(),
        }
        if simulate is not None:
            kwargs["mock_response"] = self._simulated_exception(simulate, key)
        return await self._completion(**kwargs)

    async def complete(self, messages: list[dict[str, str]], plan: FailurePlan = NO_FAILURES) -> CompletionResult:
        # A request carrying an active failure simulation is a request to exercise
        # the provider path, so it skips the cache *read* (but still writes).
        if not plan.active:
            hit = await self._cache_lookup(messages)
            if hit is not None:
                logger.info("cache hit model=%s", hit.model)
                return hit

        attempts: list[Attempt] = []
        for key in self.pool.attempt_order():
            simulate = plan.status_for(key.key_id)
            started = time.perf_counter()
            try:
                response = await self._call_key(key, messages, simulate)
                reply = _extract_text(response)
                if not reply:
                    raise EmptyCompletion("provider returned an empty completion")
            except Exception as exc:  # every upstream failure is treated as key-scoped
                status = _status_of(exc)
                reason = self._redact(" ".join(f"{type(exc).__name__}: {exc}".split()))[:240]
                attempts.append(Attempt(key.key_id, key.model, status, reason, simulated=simulate is not None))
                if simulate is None:
                    self.pool.record_failure(key, status, reason, _retry_after(exc))
                logger.warning(
                    "key failed key_id=%s status=%s simulated=%s after %.0fms: %s",
                    key.key_id,
                    status,
                    simulate is not None,
                    (time.perf_counter() - started) * 1000,
                    reason,
                )
                continue

            self.pool.record_success(key)
            await self._cache_store(response, key.model, messages)
            usage = getattr(response, "usage", None)
            logger.info("live inference key_id=%s model=%s fallback_attempts=%d", key.key_id, key.model, len(attempts))
            return CompletionResult(
                reply=reply,
                resolution_source=LIVE_INFERENCE,
                model=key.model,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                key_id=key.key_id,
                fallback_attempts=len(attempts),
                attempts=attempts,
            )

        logger.error("all %d keys failed: %s", len(attempts), [a.as_dict() for a in attempts])
        raise AllProvidersUnavailable(attempts)


# ---------------------------------------------------------------- helpers
def _extract_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""
    return content.strip() if isinstance(content, str) else ""


def _extract_text_from_dict(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    return content.strip() if isinstance(content, str) else ""


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    if isinstance(exc, TimeoutError):
        return 408
    return None


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None
