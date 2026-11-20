from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import litellm
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KEYS = ["sk-test-AAAA-1111", "sk-test-BBBB-2222"]


class FakeProvider:
    """Stands in for the LLM provider behind ``litellm.acompletion``.

    * Simulated failures (an Exception passed as ``mock_response`` by the gateway)
      are forwarded to the real ``litellm.acompletion`` so LiteLLM raises them.
    * ``fail[api_key] = exc`` makes a key fail "for real".
    * Usage is derived from the prompt so token assertions are meaningful.
    """

    def __init__(self, latency_s: float = 0.05) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: dict[str, Exception] = {}
        self.empty_for: set[str] = set()
        self.latency_s = latency_s

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        mock = kwargs.get("mock_response")
        if isinstance(mock, Exception):
            return await litellm.acompletion(**kwargs)
        api_key = kwargs["api_key"]
        if api_key in self.fail:
            raise self.fail[api_key]
        await asyncio.sleep(self.latency_s)
        messages = kwargs["messages"]
        reply = "" if api_key in self.empty_for else self._reply(messages)
        prompt_tokens = sum(len(m["content"].split()) + 4 for m in messages)
        return litellm.ModelResponse(
            model=kwargs["model"].split("/")[-1],
            choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}],
            usage=litellm.Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=len(reply.split()),
                total_tokens=prompt_tokens + len(reply.split()),
            ),
        )

    @staticmethod
    def _reply(messages: list[dict[str, str]]) -> str:
        last = messages[-1]["content"]
        if "what is my name" in last.lower():
            for m in messages:
                if m["role"] == "user" and "my name is" in m["content"].lower():
                    name = m["content"].lower().split("my name is", 1)[1].strip(" .!").split()[0]
                    return f"Your name is {name.capitalize()}."
            return "I don't know your name yet."
        return f"Answer to: {last}"

    @property
    def live_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if not isinstance(c.get("mock_response"), Exception)]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        api_keys=list(KEYS),
        default_model="gemini/gemini-2.5-flash",
        key_models=[],
        database_path=str(tmp_path / "app.db"),
        allowed_origins=["http://localhost:3000"],
        enable_failure_simulation=True,
        default_system_prompt="You are a test assistant.",
        cache_enabled=True,
        cache_type="local",
        cache_dir=str(tmp_path / "cache"),
        cache_ttl_seconds=3600,
        llm_timeout_seconds=5,
        llm_temperature=0.3,
        llm_max_output_tokens=256,
        history_max_messages=40,
        rate_limit_cooldown_seconds=30,
        auth_error_cooldown_seconds=600,
        log_level="WARNING",
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_client(settings, provider):
    clients: list[TestClient] = []

    def _make(**overrides: Any) -> TestClient:
        app = create_app(replace(settings, **overrides), completion_fn=provider)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def chat(client: TestClient, session_id: str, message: str, headers: dict | None = None, **extra: Any):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})
