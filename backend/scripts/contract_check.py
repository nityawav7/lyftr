"""Black-box check of the API contract against a running backend (local or deployed).

    python backend/scripts/contract_check.py http://localhost:8000
    python backend/scripts/contract_check.py https://your-backend.onrender.com

Standard library only. Uses real LLM calls (about 6), so the target needs
working keys and ENABLE_FAILURE_SIMULATION=true for the failover checks.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
results: list[tuple[bool, str, str]] = []


def call(method: str, path: str, body: object | None = None, headers: dict | None = None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            status, text = res.status, res.read().decode()
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read().decode()
    elapsed = (time.perf_counter() - started) * 1000
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {"_raw": text[:200]}
    return status, payload, elapsed


def check(ok: bool, name: str, info: str = "") -> bool:
    results.append((bool(ok), name, info))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({info})" if info else ""))
    return bool(ok)


def sid() -> str:
    return f"s-check-{uuid.uuid4().hex[:10]}"


def main() -> int:
    print(f"Checking {BASE}\n")

    status, health, _ = call("GET", "/health")
    check(status == 200 and health.get("status") == "ok", "GET /health is 200 ok", str(health.get("status")))
    check(health.get("keys_configured", 0) >= 2, "at least 2 keys configured", str(health.get("keys_configured")))
    check(health.get("cache_enabled") is True, "cache enabled")

    question = f"In one short sentence, what is the capital of France? (check {uuid.uuid4().hex[:6]})"
    s1, s2 = sid(), sid()
    status, live, _ = call("POST", "/chat", {"session_id": s1, "message": question})
    m = live.get("metadata", {})
    check(status == 200 and live.get("reply"), "POST /chat returns a reply", f"HTTP {status}")
    check(m.get("resolution_source") == "LIVE_INFERENCE", "first call is LIVE_INFERENCE", m.get("resolution_source"))
    check(isinstance(m.get("latency_ms"), int), "latency_ms is an integer", str(m.get("latency_ms")))
    check(
        m.get("input_tokens", 0) > 0 and m.get("output_tokens", 0) > 0,
        "tokens reported",
        f"in={m.get('input_tokens')} out={m.get('output_tokens')}",
    )
    check(m.get("key_id", "").startswith("key_"), "key_id is an id, not a secret", str(m.get("key_id")))

    status, hit, _ = call("POST", "/chat", {"session_id": s2, "message": question})
    h = hit.get("metadata", {})
    check(
        h.get("resolution_source") == "LLM_CACHE",
        "same first message in a new session is LLM_CACHE",
        h.get("resolution_source"),
    )
    check(hit.get("reply") == live.get("reply"), "cache hit returns the identical reply")
    check(h.get("key_id") is None and h.get("fallback_attempts") == 0, "cache hit: key_id null, fallback 0")
    check(
        h.get("latency_ms", 1e9) < m.get("latency_ms", 0),
        "cache hit is faster than live",
        f"{h.get('latency_ms')} ms vs {m.get('latency_ms')} ms",
    )
    check(h.get("input_tokens") == m.get("input_tokens"), "cache hit reports the stored usage")

    status, other, _ = call("POST", "/chat", {"session_id": sid(), "message": question + " Answer in French."})
    check(other.get("metadata", {}).get("resolution_source") == "LIVE_INFERENCE", "a different message is never cached")

    status, follow, _ = call("POST", "/chat", {"session_id": s1, "message": "What city did you just name? One word."})
    check(
        status == 200 and "paris" in follow.get("reply", "").lower(),
        "multi-turn: the bot remembers the session",
        follow.get("reply", "")[:60],
    )

    status, hist, _ = call("GET", f"/sessions/{s1}/messages")
    roles = [x.get("role") for x in hist.get("messages", [])]
    check(
        status == 200 and roles == ["user", "assistant", "user", "assistant"],
        "history is stored, oldest first",
        ",".join(roles),
    )
    check("metadata" in hist["messages"][1], "assistant messages carry metadata")

    for code in ("429", "500"):
        status, fo, _ = call(
            "POST",
            "/chat",
            {"session_id": sid(), "message": f"Say the word ok. ({uuid.uuid4().hex[:6]})"},
            headers={"X-Simulate-Failure": f"key_1={code}"},
        )
        fm = fo.get("metadata", {})
        check(
            status == 200 and fm.get("key_id") == "key_2" and fm.get("fallback_attempts") == 1,
            f"X-Simulate-Failure key_1={code} fails over to key_2",
            f"HTTP {status} key_id={fm.get('key_id')}",
        )

    s3 = sid()
    status, down, _ = call(
        "POST", "/chat", {"session_id": s3, "message": "hello"}, headers={"X-Simulate-Failure": "*=429"}
    )
    check(status == 503 and down.get("detail") == "all providers unavailable", "*=429 returns 503", f"HTTP {status}")
    status, _, _ = call("GET", f"/sessions/{s3}/messages")
    check(status == 404, "the failed request was not added to history", f"HTTP {status}")

    for name, body in [
        ("empty message", {"session_id": "s-x", "message": "   "}),
        ("missing session_id", {"message": "hi"}),
        ("message over 4000 chars", {"session_id": "s-x", "message": "a" * 4001}),
    ]:
        status, err, _ = call("POST", "/chat", body)
        check(status == 422 and "detail" in err, f"422 for {name}", f"HTTP {status}")
    status, err, _ = call("POST", "/chat", raw=b'{"session_id": "s-x", "message": ')
    check(status == 422 and "detail" in err, "422 for malformed JSON", f"HTTP {status}")

    status, err, _ = call("GET", f"/sessions/{sid()}/messages")
    check(status == 404 and err.get("detail") == "session not found", "404 for an unknown session")

    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
