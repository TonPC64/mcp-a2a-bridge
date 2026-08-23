"""SQLite-backed A2A task storage for restart-safe long-running agents."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task


class SQLiteTaskStore(TaskStore):
    """Persist A2A tasks as protobuf bytes with TTL and LRU eviction."""

    def __init__(self, path: Path, ttl: int = 86400, maxsize: int = 1000) -> None:
        self._path = Path(path).expanduser()
        self._ttl = ttl
        self._maxsize = maxsize
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    payload BLOB NOT NULL,
                    updated_at REAL NOT NULL,
                    accessed_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    def _evict(self, connection: sqlite3.Connection) -> None:
        cutoff = time.time() - self._ttl
        connection.execute("DELETE FROM tasks WHERE updated_at < ?", (cutoff,))
        connection.execute(
            """
            DELETE FROM tasks WHERE task_id IN (
                SELECT task_id FROM tasks
                ORDER BY accessed_at ASC
                LIMIT MAX(0, (SELECT COUNT(*) FROM tasks) - ?)
            )
            """,
            (self._maxsize,),
        )

    async def save(self, task: Task, context: ServerCallContext) -> None:
        now = time.time()
        async with self._lock:
            with sqlite3.connect(self._path) as connection:
                connection.execute(
                    """
                    INSERT INTO tasks(task_id, payload, updated_at, accessed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        payload=excluded.payload,
                        updated_at=excluded.updated_at,
                        accessed_at=excluded.accessed_at
                    """,
                    (task.id, task.SerializeToString(), now, now),
                )
                self._evict(connection)
                connection.commit()

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        async with self._lock:
            with sqlite3.connect(self._path) as connection:
                self._evict(connection)
                row = connection.execute(
                    "SELECT payload FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    "UPDATE tasks SET accessed_at = ? WHERE task_id = ?",
                    (time.time(), task_id),
                )
                connection.commit()
        task = Task()
        task.ParseFromString(row[0])
        return task

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        async with self._lock:
            with sqlite3.connect(self._path) as connection:
                self._evict(connection)
                rows = connection.execute(
                    "SELECT payload FROM tasks ORDER BY updated_at DESC"
                ).fetchall()
                connection.commit()
        tasks = []
        for (payload,) in rows:
            task = Task()
            task.ParseFromString(payload)
            tasks.append(task)
        return ListTasksResponse(tasks=tasks)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        async with self._lock:
            with sqlite3.connect(self._path) as connection:
                connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                connection.commit()
