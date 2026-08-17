"""kitegen.checkpoint — State persistence for graph execution.

Default: MemorySaver (in-memory dict, sessions lost on restart).
Optional: PostgresSaver (~40 lines of SQL, add yourself).

Interface:
    class Checkpointer:
        async def save(self, state: dict, thread_id: str) -> None: ...
        async def load(self, thread_id: str) -> dict | None: ...
"""

from __future__ import annotations

import json


class Checkpointer:
    """Abstract interface for state persistence."""

    async def save(self, state: dict, thread_id: str) -> None:
        raise NotImplementedError

    async def load(self, thread_id: str) -> dict | None:
        raise NotImplementedError


class MemorySaver(Checkpointer):
    """Store state in an in-memory dict. Lost on process restart."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    async def save(self, state: dict, thread_id: str) -> None:
        # Store a copy — shallow copy is fine for plain dicts
        self._store[thread_id] = dict(state)

    async def load(self, thread_id: str) -> dict | None:
        saved = self._store.get(thread_id)
        return dict(saved) if saved else None


class PostgresSaver(Checkpointer):
    """Store state in PostgreSQL. One row per checkpoint per thread.

    Requires: pip install psycopg

    Table setup (run once):
        CREATE TABLE IF NOT EXISTS kitegen_checkpoints (
            thread_id TEXT PRIMARY KEY,
            state JSONB NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
    """

    def __init__(self, conn_string: str):
        self._conn_string = conn_string

    async def save(self, state: dict, thread_id: str) -> None:
        import psycopg
        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            await conn.execute(
                """
                INSERT INTO kitegen_checkpoints (thread_id, state, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (thread_id) DO UPDATE
                SET state = EXCLUDED.state, updated_at = now()
                """,
                (thread_id, json.dumps(state)),
            )

    async def load(self, thread_id: str) -> dict | None:
        import psycopg
        async with await psycopg.AsyncConnection.connect(self._conn_string) as conn:
            row = await conn.fetchrow(
                "SELECT state FROM kitegen_checkpoints WHERE thread_id = %s",
                (thread_id,),
            )
            return json.loads(row["state"]) if row else None
