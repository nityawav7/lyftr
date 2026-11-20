"""Caching accuracy, per-request token accounting and multi-turn session state."""

from __future__ import annotations

import asyncio

import httpx

from app.main import create_app

from .conftest import chat


# ------------------------------------------------------------------ caching
def test_same_first_message_in_new_session_is_cache_hit(client, provider):
    live = chat(client, "s-c1", "What is the capital of France?").json()
    hit = chat(client, "s-c2", "What is the capital of France?").json()

    assert live["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert hit["metadata"]["resolution_source"] == "LLM_CACHE"
    assert hit["reply"] == live["reply"]
    assert hit["metadata"]["key_id"] is None
    assert hit["metadata"]["fallback_attempts"] == 0
    assert hit["metadata"]["model"] == live["metadata"]["model"]
    # tokens are the usage stored with the cached response
    assert hit["metadata"]["input_tokens"] == live["metadata"]["input_tokens"]
    assert hit["metadata"]["output_tokens"] == live["metadata"]["output_tokens"]
    # cache hit is faster and never reached the provider
    assert hit["metadata"]["latency_ms"] < live["metadata"]["latency_ms"]
    assert len(provider.live_calls) == 1


def test_different_message_is_never_cached(client):
    chat(client, "s-d1", "What is the capital of France?")
    res = chat(client, "s-d2", "What is the capital of Spain?").json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_key_includes_history(client):
    chat(client, "s-h1", "hello")
    chat(client, "s-h1", "tell me more")
    # same new message, but a different history -> must be live
    chat(client, "s-h2", "something else")
    res = chat(client, "s-h2", "tell me more").json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_key_includes_system_prompt(client):
    chat(client, "s-p1", "hi", system_prompt="Be a pirate.")
    res = chat(client, "s-p2", "hi", system_prompt="Be a poet.").json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    res = chat(client, "s-p3", "hi", system_prompt="Be a pirate.").json()
    assert res["metadata"]["resolution_source"] == "LLM_CACHE"


def test_cache_hit_is_stored_in_history_with_cache_label(client):
    chat(client, "s-l1", "repeat me")
    chat(client, "s-l2", "repeat me")
    msgs = client.get("/sessions/s-l2/messages").json()["messages"]
    assert msgs[1]["metadata"]["resolution_source"] == "LLM_CACHE"


def test_reply_from_key2_is_a_hit_for_later_requests(client):
    chat(client, "s-k1", "shared", headers={"X-Simulate-Failure": "key_1=429"})
    res = chat(client, "s-k2", "shared").json()
    assert res["metadata"]["resolution_source"] == "LLM_CACHE"


def test_cache_disabled(make_client):
    c = make_client(cache_enabled=False)
    assert c.get("/health").json()["cache_enabled"] is False
    chat(c, "s-n1", "same")
    assert chat(c, "s-n2", "same").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_disk_cache_survives_restart(make_client, tmp_path):
    a = make_client(cache_type="disk", cache_dir=str(tmp_path / "disk"))
    live = chat(a, "s-disk1", "persist me").json()
    b = make_client(cache_type="disk", cache_dir=str(tmp_path / "disk"))
    hit = chat(b, "s-disk2", "persist me").json()
    assert hit["metadata"]["resolution_source"] == "LLM_CACHE"
    assert hit["reply"] == live["reply"]


# ------------------------------------------------------------------- tokens
def test_tokens_are_per_request_not_cumulative(client, provider):
    first = chat(client, "s-t1", "alpha beta gamma").json()["metadata"]
    other = chat(client, "s-t2", "delta epsilon zeta").json()["metadata"]
    assert first["input_tokens"] == other["input_tokens"]

    second = chat(client, "s-t1", "one more").json()["metadata"]
    # input grows because history is sent, but the output is only this reply's
    assert second["input_tokens"] > first["input_tokens"]
    assert second["output_tokens"] == len("Answer to: one more".split())
    expected = sum(len(m["content"].split()) + 4 for m in provider.live_calls[-1]["messages"])
    assert second["input_tokens"] == expected


# ----------------------------------------------------------------- sessions
def test_bot_remembers_earlier_messages(client, provider):
    chat(client, "s-mem", "Hi, my name is Nitya.")
    res = chat(client, "s-mem", "What is my name?").json()
    assert res["reply"] == "Your name is Nitya."
    sent = provider.live_calls[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]


def test_sessions_are_isolated(client):
    chat(client, "s-iso-a", "my name is Asha")
    res = chat(client, "s-iso-b", "what is my name?").json()
    assert res["reply"] == "I don't know your name yet."


def test_system_prompt_persists_for_session(client, provider):
    chat(client, "s-sys", "hi", system_prompt="You only answer in haiku.")
    chat(client, "s-sys", "and now?")
    assert provider.live_calls[-1]["messages"][0] == {"role": "system", "content": "You only answer in haiku."}
    chat(client, "s-default", "hi")
    assert provider.live_calls[-1]["messages"][0]["content"] == "You are a test assistant."


def test_history_window_limits_context(make_client, provider):
    c = make_client(history_max_messages=2)
    for i in range(3):
        chat(c, "s-win", f"message {i}")
    sent = provider.live_calls[-1]["messages"]
    assert len(sent) == 1 + 2 + 1  # system + last 2 stored messages + new one
    assert len(c.get("/sessions/s-win/messages").json()["messages"]) == 6  # full history still stored


def test_history_survives_app_restart(make_client):
    a = make_client()
    chat(a, "s-restart", "remember this")
    b = make_client()  # same DATABASE path, new process-equivalent
    msgs = b.get("/sessions/s-restart/messages").json()["messages"]
    assert msgs[0]["content"] == "remember this"


async def test_concurrent_messages_in_one_session_are_serialised(settings, provider):
    app = create_app(settings, completion_fn=provider)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            results = await asyncio.gather(
                *[ac.post("/chat", json={"session_id": "s-conc", "message": f"msg {i}"}) for i in range(4)]
            )
            assert all(r.status_code == 200 for r in results)
            history = (await ac.get("/sessions/s-conc/messages")).json()["messages"]
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant"] * 4
    # each later call saw every earlier turn
    assert sorted(len(c["messages"]) for c in provider.live_calls) == [2, 4, 6, 8]
