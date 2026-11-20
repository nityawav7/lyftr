"""Contract tests: /health, /chat, /sessions/{id}/messages, validation and errors."""

from __future__ import annotations

import pytest

from .conftest import KEYS, chat


# ------------------------------------------------------------------ health
def test_health_reports_contract_fields(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True


def test_health_is_503_without_keys(make_client):
    res = make_client(api_keys=[]).get("/health")
    assert res.status_code == 503
    assert "detail" in res.json()


def test_health_never_leaks_secrets(client):
    text = client.get("/health").text
    for key in KEYS:
        assert key not in text


# -------------------------------------------------------------------- chat
def test_chat_response_shape(client):
    res = chat(client, "s-shape", "What is the capital of France?")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "s-shape"
    assert body["reply"]
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/gemini-2.5-flash"
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
    assert isinstance(meta["input_tokens"], int) and meta["input_tokens"] > 0
    assert isinstance(meta["output_tokens"], int) and meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


def test_chat_trims_message(client, provider):
    chat(client, "s-trim", "   hello there   ")
    assert provider.live_calls[-1]["messages"][-1]["content"] == "hello there"


def test_secret_is_never_returned(client):
    text = chat(client, "s-secret", "hi").text
    for key in KEYS:
        assert key not in text


# -------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s-1"},
        {"message": "hi"},
        {"session_id": "s-1", "message": ""},
        {"session_id": "s-1", "message": "   \n\t "},
        {"session_id": "", "message": "hi"},
        {"session_id": "x" * 129, "message": "hi"},
        {"session_id": "s-1", "message": "a" * 4001},
        {"session_id": "s-1", "message": 42},
        {"session_id": "s-1", "message": None},
    ],
)
def test_invalid_payloads_return_422_json(client, payload):
    res = client.post("/chat", json=payload)
    assert res.status_code == 422
    assert "detail" in res.json()


def test_malformed_json_returns_422(client):
    res = client.post(
        "/chat", content=b'{"session_id": "s-1", "message": ', headers={"Content-Type": "application/json"}
    )
    assert res.status_code == 422
    assert "detail" in res.json()


def test_boundaries_are_accepted(client):
    assert chat(client, "x" * 128, "a" * 4000).status_code == 200
    # 4000 chars after trimming is still valid even with surrounding whitespace
    assert chat(client, "s-bound", "  " + "b" * 4000 + "  ").status_code == 200


def test_failed_validation_is_not_stored(client):
    client.post("/chat", json={"session_id": "s-val", "message": ""})
    assert client.get("/sessions/s-val/messages").status_code == 404


# ----------------------------------------------------------------- history
def test_history_unknown_session_is_404(client):
    res = client.get("/sessions/does-not-exist/messages")
    assert res.status_code == 404
    assert res.json() == {"detail": "session not found"}


def test_history_shape_and_order(client):
    chat(client, "s-hist", "first question")
    chat(client, "s-hist", "second question")
    res = client.get("/sessions/s-hist/messages")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "s-hist"
    msgs = body["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "first question"
    assert msgs[2]["content"] == "second question"
    assert "metadata" not in msgs[0]
    meta = msgs[1]["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert {"input_tokens", "output_tokens", "model", "latency_ms", "key_id"} <= meta.keys()
    # system prompt is never part of the history
    assert all(m["role"] in ("user", "assistant") for m in msgs)


def test_unknown_route_is_json(client):
    res = client.get("/nope")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")
    assert "detail" in res.json()


def test_cors_allows_frontend_origin_and_simulation_header(client):
    res = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-simulate-failure" in res.headers["access-control-allow-headers"].lower()
