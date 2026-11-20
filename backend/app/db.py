"""SQLite persistence for sessions and messages.

Design notes
------------
* stdlib ``sqlite3`` only, no ORM: the schema is three statements and the
  queries are trivial, so an ORM would add weight without adding safety.
* One short-lived connection per operation. SQLite connections are cheap to
  open, and this keeps every call thread-safe when FastAPI runs it in its
  threadpool (``anyio.to_thread``) so the event loop never blocks on disk I/O.
* WAL journal mode so reads (history endpoint) never block on a write.
* A user/assistant pair is written in ONE transaction, and only after the LLM
  call succeeded. A failed request (422/503) therefore leaves no trace.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    system_prompt  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT NOT NULL,
    metadata     TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id, id);
"""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        # A shared in-memory DB needs a URI + a connection kept open for its lifetime.
        self._memory_keepalive: sqlite3.Connection | None = None
        if path == ":memory:":
            self._uri = "file:lyftr_mem?mode=memory&cache=shared"
            self._memory_keepalive = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        else:
            self._uri = None

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        if self._uri:
            conn = sqlite3.connect(self._uri, uri=True, timeout=10, check_same_thread=False)
        else:
            conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            if self.path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        try:
            with self.connection() as conn:
                conn.execute("SELECT 1 FROM messages LIMIT 1").fetchall()
            return True
        except sqlite3.Error:
            return False

    # --------------------------------------------------------------- sessions
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id, system_prompt, created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_history(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Messages for a session, oldest first. ``limit`` keeps only the newest N."""
        with self.connection() as conn:
            if limit is not None and limit > 0:
                rows = conn.execute(
                    """
                    SELECT role, content, metadata, created_at FROM (
                        SELECT id, role, content, metadata, created_at FROM messages
                        WHERE session_id = ? ORDER BY id DESC LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT role, content, metadata, created_at FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        history = []
        for row in rows:
            item: dict[str, Any] = {"role": row["role"], "content": row["content"], "created_at": row["created_at"]}
            if row["metadata"]:
                item["metadata"] = json.loads(row["metadata"])
            history.append(item)
        return history

    def save_exchange(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        metadata: dict[str, Any],
        system_prompt: str | None,
    ) -> None:
        """Atomically upsert the session and append the user + assistant turn."""
        now = utc_now_iso()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, system_prompt, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt),
                    updated_at    = excluded.updated_at
                """,
                (session_id, system_prompt, now, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_message, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, assistant_message, json.dumps(metadata), utc_now_iso()),
            )
