"""The API-key pool: ordered keys, stable ids and per-key health.

Each key gets a stable public id (``key_1``, ``key_2`` ... in the order they
appear in ``LLM_API_KEYS``). The raw secret never leaves this module except
when it is handed to LiteLLM for the actual call.

Health / cooldown
-----------------
When a key fails for real (not simulated), it is put on a short cooldown:

* 429 / quota exhaustion -> ``rate_limit_cooldown`` seconds (or the provider's
  ``Retry-After`` if it sent one, capped at 5 minutes)
* 401 / 403 (revoked or invalid key)  -> ``auth_cooldown`` seconds
* 5xx / timeouts are treated as transient: no cooldown

A cooling key is *de-prioritised*, never removed: the pool returns healthy
keys first in configured order, then cooling keys. So a burst of 429s on
key_1 stops costing every subsequent request a wasted round-trip, but if
every key is cooling we still try them all before answering 503.
Simulated failures never touch health state, so the test hook cannot leak
into later requests.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class ApiKey:
    key_id: str
    secret: str
    model: str
    cooldown_until: float = 0.0
    last_error: str | None = None
    successes: int = 0
    failures: int = 0

    def cooling(self, now: float) -> bool:
        return self.cooldown_until > now

    def __repr__(self) -> str:  # never print the secret, even by accident
        return f"ApiKey(key_id={self.key_id!r}, model={self.model!r})"


class KeyPool:
    def __init__(
        self,
        secrets: list[str],
        models: list[str],
        rate_limit_cooldown: int = 30,
        auth_cooldown: int = 600,
    ) -> None:
        if len(secrets) != len(models):
            raise ValueError("secrets and models must be aligned")
        self._keys = [
            ApiKey(key_id=f"key_{i + 1}", secret=secret, model=model)
            for i, (secret, model) in enumerate(zip(secrets, models, strict=True))
        ]
        self._rate_limit_cooldown = rate_limit_cooldown
        self._auth_cooldown = auth_cooldown
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def keys(self) -> list[ApiKey]:
        return list(self._keys)

    def distinct_models(self) -> list[str]:
        seen: list[str] = []
        for key in self._keys:
            if key.model not in seen:
                seen.append(key.model)
        return seen

    def attempt_order(self) -> list[ApiKey]:
        """Healthy keys in configured order, then cooling keys (soonest-available first)."""
        now = time.monotonic()
        with self._lock:
            healthy = [k for k in self._keys if not k.cooling(now)]
            cooling = sorted((k for k in self._keys if k.cooling(now)), key=lambda k: k.cooldown_until)
        return healthy + cooling

    def record_success(self, key: ApiKey) -> None:
        with self._lock:
            key.successes += 1
            key.cooldown_until = 0.0
            key.last_error = None

    def record_failure(self, key: ApiKey, status: int | None, reason: str, retry_after: float | None = None) -> None:
        with self._lock:
            key.failures += 1
            key.last_error = reason
            if status == 429:
                seconds = min(retry_after, 300) if retry_after else self._rate_limit_cooldown
            elif status in (401, 403):
                seconds = self._auth_cooldown
            else:
                seconds = 0
            if seconds:
                key.cooldown_until = max(key.cooldown_until, time.monotonic() + seconds)

    def snapshot(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "key_id": k.key_id,
                    "model": k.model,
                    "status": "cooling_down" if k.cooling(now) else "healthy",
                    "cooldown_remaining_s": max(0, round(k.cooldown_until - now)) if k.cooling(now) else 0,
                    "successes": k.successes,
                    "failures": k.failures,
                    "last_error": k.last_error,
                }
                for k in self._keys
            ]
