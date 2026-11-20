"""Parsing for the ``X-Simulate-Failure`` test hook.

Format: comma-separated ``<key_id>=<status>`` pairs, ``*`` meaning every key.

    key_1=429
    key_1=429,key_2=500
    *=429

Supported statuses: 429, 500, 502, 503. Malformed pairs are ignored rather
than rejected: the hook is a demo/test aid and must never be the reason a
real chat request fails. A specific key entry wins over ``*``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SUPPORTED_STATUSES = frozenset({429, 500, 502, 503})


@dataclass(frozen=True)
class FailurePlan:
    per_key: dict[str, int] = field(default_factory=dict)
    wildcard: int | None = None
    ignored: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.per_key) or self.wildcard is not None

    def status_for(self, key_id: str) -> int | None:
        if key_id in self.per_key:
            return self.per_key[key_id]
        return self.wildcard


NO_FAILURES = FailurePlan()


def parse_failure_header(raw: str | None) -> FailurePlan:
    if not raw or not raw.strip():
        return NO_FAILURES

    per_key: dict[str, int] = {}
    wildcard: int | None = None
    ignored: list[str] = []

    for chunk in raw.split(","):
        part = chunk.strip()
        if not part:
            continue
        key, sep, status_raw = part.partition("=")
        key, status_raw = key.strip(), status_raw.strip()
        if not sep or not key or not status_raw.isdigit() or int(status_raw) not in SUPPORTED_STATUSES:
            ignored.append(part)
            continue
        status = int(status_raw)
        if key == "*":
            wildcard = status
        else:
            per_key[key.lower()] = status

    return FailurePlan(per_key=per_key, wildcard=wildcard, ignored=tuple(ignored))
