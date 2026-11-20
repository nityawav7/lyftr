"""Key pool failover: simulated (X-Simulate-Failure) and real provider failures."""

from __future__ import annotations

import litellm
import pytest

from .conftest import KEYS, chat

M = "gemini/gemini-2.5-flash"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_key1_failure_falls_back_to_key2(client, status):
    res = chat(client, f"s-sim-{status}", "hello", headers={"X-Simulate-Failure": f"key_1={status}"})
    assert res.status_code == 200
    meta = res.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["attempts"][0]["status"] == status and meta["attempts"][0]["simulated"] is True
    # a real answer, not an error wrapped in a 200
    assert res.json()["reply"].startswith("Answer to:")


@pytest.mark.parametrize("header", ["*=429", "key_1=429,key_2=500", "*=503"])
def test_all_keys_failing_returns_503_and_stores_nothing(client, header):
    res = chat(client, "s-down", "hello", headers={"X-Simulate-Failure": header})
    assert res.status_code == 503
    assert res.json()["detail"] == "all providers unavailable"
    assert client.get("/sessions/s-down/messages").status_code == 404


def test_503_does_not_touch_existing_history(client):
    chat(client, "s-keep", "first")
    res = chat(client, "s-keep", "second", headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 503
    msgs = client.get("/sessions/s-keep/messages").json()["messages"]
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["first"]


def test_simulation_only_affects_that_request(client):
    chat(client, "s-once", "one", headers={"X-Simulate-Failure": "key_1=429"})
    res = chat(client, "s-once", "two")
    assert res.json()["metadata"]["key_id"] == "key_1"
    assert res.json()["metadata"]["fallback_attempts"] == 0


def test_specific_key_overrides_wildcard(client):
    res = chat(client, "s-wild", "hi", headers={"X-Simulate-Failure": "*=429,key_2=200"})
    # key_2=200 is not a supported status -> ignored, wildcard applies to all -> 503
    assert res.status_code == 503
    res = chat(client, "s-wild2", "hi", headers={"X-Simulate-Failure": "key_2=503,key_1=429"})
    assert res.status_code == 503


def test_simulation_disabled_ignores_header(make_client):
    c = make_client(enable_failure_simulation=False)
    res = chat(c, "s-off", "hi", headers={"X-Simulate-Failure": "*=429"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_malformed_simulation_header_is_harmless(client):
    res = chat(client, "s-bad", "hi", headers={"X-Simulate-Failure": "garbage,,key_1=abc,=429"})
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_simulated_request_skips_cache_read(client):
    chat(client, "s-a", "cache me")
    res = chat(client, "s-b", "cache me", headers={"X-Simulate-Failure": "key_1=429"})
    meta = res.json()["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["key_id"] == "key_2"


# ---------------------------------------------------------- real failures
def test_real_rate_limit_fails_over_and_cools_key_down(client, provider):
    provider.fail[KEYS[0]] = litellm.RateLimitError("quota exceeded", llm_provider="gemini", model=M)
    res = chat(client, "s-real", "hello")
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_2"
    assert res.json()["metadata"]["fallback_attempts"] == 1

    # key_1 is now cooling down: the next request goes straight to key_2
    res = chat(client, "s-real", "again")
    assert res.json()["metadata"]["key_id"] == "key_2"
    assert res.json()["metadata"]["fallback_attempts"] == 0
    keys = {k["key_id"]: k for k in client.get("/health").json()["keys"]}
    assert keys["key_1"]["status"] == "cooling_down"
    assert keys["key_2"]["status"] == "healthy"


def test_cooling_keys_are_still_tried_last(client, provider):
    provider.fail[KEYS[0]] = litellm.RateLimitError("quota exceeded", llm_provider="gemini", model=M)
    chat(client, "s-cool", "one")  # key_1 now cooling
    provider.fail.pop(KEYS[0])
    provider.fail[KEYS[1]] = litellm.InternalServerError("boom", llm_provider="gemini", model=M)
    res = chat(client, "s-cool", "two")
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_1"


def test_real_5xx_is_transient_no_cooldown(client, provider):
    provider.fail[KEYS[0]] = litellm.ServiceUnavailableError("overloaded", llm_provider="gemini", model=M)
    assert chat(client, "s-5xx", "one").json()["metadata"]["key_id"] == "key_2"
    provider.fail.clear()
    assert chat(client, "s-5xx", "two").json()["metadata"]["key_id"] == "key_1"


def test_auth_error_and_timeout_fail_over(client, provider):
    provider.fail[KEYS[0]] = litellm.AuthenticationError("bad key", llm_provider="gemini", model=M)
    assert chat(client, "s-auth", "hi").json()["metadata"]["key_id"] == "key_2"
    provider.fail[KEYS[0]] = litellm.Timeout("slow", model=M, llm_provider="gemini")
    assert chat(client, "s-auth2", "hey").json()["metadata"]["key_id"] == "key_2"


def test_empty_completion_fails_over(client, provider):
    provider.empty_for.add(KEYS[0])
    res = chat(client, "s-empty", "hi")
    assert res.status_code == 200
    assert res.json()["reply"]
    assert res.json()["metadata"]["key_id"] == "key_2"


def test_all_real_failures_return_503(client, provider):
    for key in KEYS:
        provider.fail[key] = litellm.RateLimitError("quota", llm_provider="gemini", model=M)
    res = chat(client, "s-all", "hi")
    assert res.status_code == 503
    assert res.json()["detail"] == "all providers unavailable"
    assert KEYS[0] not in res.text and KEYS[1] not in res.text


def test_provider_error_messages_are_redacted(client, provider):
    provider.fail[KEYS[0]] = litellm.InternalServerError(
        f"upstream said: invalid key {KEYS[0]}", llm_provider="gemini", model=M
    )
    res = chat(client, "s-redact", "hi")
    assert KEYS[0] not in res.text
    assert "***" in res.json()["metadata"]["attempts"][0]["error"]


def test_each_key_can_use_its_own_model(make_client, provider):
    c = make_client(key_models=["gemini/gemini-2.5-flash", "groq/llama-3.1-8b-instant"])
    res = chat(c, "s-multi", "hi", headers={"X-Simulate-Failure": "key_1=429"})
    assert res.json()["metadata"]["model"] == "groq/llama-3.1-8b-instant"
    assert provider.live_calls[-1]["model"] == "groq/llama-3.1-8b-instant"
    assert provider.live_calls[-1]["num_retries"] == 0


def test_temperature_is_only_sent_when_configured(make_client, provider):
    chat(make_client(llm_temperature=None), "s-temp-off", "hi")
    assert "temperature" not in provider.live_calls[-1]
    chat(make_client(llm_temperature=0.3), "s-temp-on", "hi there")
    assert provider.live_calls[-1]["temperature"] == 0.3
