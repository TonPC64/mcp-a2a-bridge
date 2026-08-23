"""Bounded in-memory log of A2A task activity, for dashboard observability."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

TEXT_PREVIEW_LIMIT = 500


@dataclass
class TaskActivity:
    id: str
    agent: str
    kind: str
    state: str
    text: str
    created_at: float
    updated_at: float


class ActivityLog:
    """Bounded LRU log of task activity, keyed by task id.

    Mirrors the OrderedDict LRU shape of TTLTaskStore for consistency.
    """

    def __init__(self, maxsize: int = 500) -> None:
        self._maxsize = maxsize
        self._entries: OrderedDict[str, TaskActivity] = OrderedDict()
        # A plain threading.Lock is used (not asyncio.Lock) because record()/list()
        # only do synchronous work while holding it and never await inside the
        # critical section. The main stdio MCP loop and the dashboard's uvicorn
        # loop run in different OS threads, and an asyncio.Lock is bound to the
        # loop that first awaits it, so cross-thread contention could hang the
        # primary stdio bridge. threading.Lock works safely across threads.
        self._lock = threading.Lock()

    async def record(
        self,
        *,
        task_id: str | None,
        agent: str,
        kind: str,
        state: str,
        text: str,
    ) -> TaskActivity:
        with self._lock:
            key = task_id or uuid.uuid4().hex
            now = time.time()
            preview = text[:TEXT_PREVIEW_LIMIT]

            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                created_at = existing.created_at
            else:
                if len(self._entries) >= self._maxsize:
                    self._entries.popitem(last=False)  # evict oldest
                created_at = now

            entry = TaskActivity(
                id=key,
                agent=agent,
                kind=kind,
                state=state,
                text=preview,
                created_at=created_at,
                updated_at=now,
            )
            self._entries[key] = entry
            return entry

    async def list(self) -> list[TaskActivity]:
        with self._lock:
            return list(reversed(self._entries.values()))