"""One chat turn: resolve the system prompt, build context, call the gateway, persist."""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import Any

from anyio import to_thread

from .config import Settings
from .db import Database
from .llm.gateway import LLMGateway
from .llm.simulation import FailurePlan
from .schemas import ChatRequest


class ChatService:
    def __init__(self, settings: Settings, db: Database, gateway: LLMGateway) -> None:
        self.settings = settings
        self.db = db
        self.gateway = gateway
        # One lock per active session: two concurrent messages in the same chat
        # are processed in order, so neither is answered without the other in
        # its context. Weak values -> idle locks are garbage-collected.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def handle(self, request: ChatRequest, plan: FailurePlan, started: float) -> dict[str, Any]:
        lock = self._lock_for(request.session_id)
        async with lock:
            session = await to_thread.run_sync(self.db.get_session, request.session_id)
            system_prompt = (
                request.system_prompt or (session or {}).get("system_prompt") or self.settings.default_system_prompt
            )
            history = await to_thread.run_sync(
                self.db.get_history, request.session_id, self.settings.history_max_messages
            )

            messages = [{"role": "system", "content": system_prompt}]
            messages += [{"role": m["role"], "content": m["content"]} for m in history]
            messages.append({"role": "user", "content": request.message})

            # Raises AllProvidersUnavailable -> 503, before anything is persisted.
            result = await self.gateway.complete(messages, plan)

            metadata = {
                "resolution_source": result.resolution_source,
                "model": result.model,
                "latency_ms": int(round((time.perf_counter() - started) * 1000)),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "key_id": result.key_id,
                "fallback_attempts": result.fallback_attempts,
                "attempts": [a.as_dict() for a in result.attempts],
            }
            await to_thread.run_sync(
                self.db.save_exchange,
                request.session_id,
                request.message,
                result.reply,
                metadata,
                request.system_prompt,
            )

        return {"session_id": request.session_id, "reply": result.reply, "metadata": metadata}
