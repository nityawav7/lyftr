"""Run the real API with a fake LLM provider (no API keys, no network).

For UI development and offline demos only:

    python -m scripts.fake_provider_server   # from backend/, serves on :8000

Everything except the provider call is real: SQLite, the LiteLLM cache,
failover, X-Simulate-Failure, validation and error handling.
"""

import asyncio
import os
import random

import litellm
import uvicorn

os.environ.setdefault("LLM_API_KEYS", "fake-key-one,fake-key-two")
os.environ.setdefault("ENABLE_FAILURE_SIMULATION", "true")
os.environ.setdefault("CACHE_TYPE", "local")

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


async def fake_completion(**kwargs):
    if isinstance(kwargs.get("mock_response"), Exception):
        return await litellm.acompletion(**kwargs)  # LiteLLM raises the simulated error
    await asyncio.sleep(random.uniform(0.6, 1.2))
    messages = kwargs["messages"]
    last = messages[-1]["content"]
    turns = sum(1 for m in messages if m["role"] == "user")
    earlier = " ".join(m["content"] for m in messages[:-1] if m["role"] == "assistant")
    if "capital of france" in last.lower():
        reply = "The capital of France is Paris."
    elif "what city" in last.lower() and "Paris" in earlier:
        reply = "Paris."
    else:
        reply = (
            f"You asked: **{last}**\n\nThis is a fake provider reply (turn {turns} of this chat). "
            "Run with real keys for actual answers."
        )
    prompt_tokens = sum(len(m["content"]) // 4 + 4 for m in kwargs["messages"])
    return litellm.ModelResponse(
        model=kwargs["model"],
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}],
        usage=litellm.Usage(
            prompt_tokens=prompt_tokens, completion_tokens=len(reply) // 4, total_tokens=prompt_tokens + len(reply) // 4
        ),
    )


app = create_app(Settings.from_env(), completion_fn=fake_completion)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
