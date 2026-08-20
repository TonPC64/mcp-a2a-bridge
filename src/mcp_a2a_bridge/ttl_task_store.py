"""TaskStore with TTL eviction — prevents unbounded memory growth in long-running agents."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task


class TTLTaskStore(TaskStore):
    """In-memory TaskStore that evicts tasks older than `ttl` seconds.

    Defaults: 1 h TTL, 1 000 task cap (LRU eviction when full).
    ponytail: OrderedDict LRU + time-based TTL — no external deps.
    """

    def __init__(self, ttl: int = 3600, maxsize: int = 1000) -> None:
        self._ttl = ttl
        self._maxsize = maxsize
        self._tasks: OrderedDict[str, tuple[Task, float]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, ts) in self._tasks.items() if now - ts > self._ttl]
        for k in expired:
            del self._tasks[k]

    async def save(self, task: Task, context: ServerCallContext) -> None:
        async with self._lock:
            self._evict_expired()
            if task.id in self._tasks:
                self._tasks.move_to_end(task.id)
            elif len(self._tasks) >= self._maxsize:
                self._tasks.popitem(last=False)  # evict oldest
            self._tasks[task.id] = (task, time.monotonic())

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return None
            task, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._tasks[task_id]
                return None
            self._tasks.move_to_end(task_id)
            return task

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        async with self._lock:
            self._evict_expired()
            return ListTasksResponse(tasks=[t for t, _ in self._tasks.values()])

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        async with self._lock:
            self._tasks.pop(task_id, None)
