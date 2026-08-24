"""Bounded in-memory log of A2A task activity, for dashboard observability."""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from queue import Queue
from typing import Any

from mcp_a2a_bridge.snapshots import SnapshotSubscribers
from mcp_a2a_bridge.activity_store import SQLiteActivityStore

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

    def __init__(self, maxsize: int = 500, store: SQLiteActivityStore | None = None) -> None:
        self._maxsize = maxsize
        self._entries: OrderedDict[str, TaskActivity] = OrderedDict()
        # A plain threading.Lock is used (not asyncio.Lock) because record()/list()
        # only do synchronous work while holding it and never await inside the
        # critical section. The main stdio MCP loop and the dashboard's uvicorn
        # loop run in different OS threads, and an asyncio.Lock is bound to the
        # loop that first awaits it, so cross-thread contention could hang the
        # primary stdio bridge. threading.Lock works safely across threads.
        self._lock = threading.Lock()
        self._subscribers = SnapshotSubscribers()
        self._store = store

    async def record(
        self,
        *,
        task_id: str | None,
        agent: str,
        kind: str,
        state: str,
        text: str,
        replaces_task_id: str | None = None,
    ) -> TaskActivity:
        with self._lock:
            key = task_id or uuid.uuid4().hex
            now = time.time()
            preview = text[:TEXT_PREVIEW_LIMIT]

            replaced = None
            if replaces_task_id and replaces_task_id != key:
                replaced = self._entries.pop(replaces_task_id, None)
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                created_at = existing.created_at
            elif replaced is not None:
                created_at = replaced.created_at
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
            if self._store is not None:
                try:
                    self._store.upsert(
                        {
                            "id": entry.id,
                            "agent": entry.agent,
                            "kind": entry.kind,
                            "state": entry.state,
                            "text": entry.text,
                            "created_at": entry.created_at,
                            "updated_at": entry.updated_at,
                        }
                    )
                    if replaced is not None:
                        self._store.delete(replaces_task_id)
                except Exception as exc:
                    # A crashed/slow/misconfigured shared store must never fail
                    # or slow down the real A2A tool call (spec "Error
                    # handling"). Once a write fails, disable the store for the
                    # rest of this process's lifetime rather than retry it on
                    # every subsequent record(): a contended SQLite file keeps
                    # failing the same way, and retrying would re-pay the
                    # busy_timeout cost (finding #2) on every A2A tool call.
                    print(
                        "mcp-a2a-bridge: shared activity store write failed, "
                        f"disabling shared-store writes for this process: {exc}",
                        file=sys.stderr,
                    )
                    self._store = None
            snapshot = self._snapshot_locked()
            self._subscribers.publish(snapshot)
            return entry

    async def list(self) -> list[TaskActivity]:
        with self._lock:
            return list(reversed(self._entries.values()))

    async def replace_all(self, entries: list[TaskActivity]) -> None:
        """Atomically swap the in-memory view and publish one snapshot.

        Used by the standalone dashboard service's poll loop (see
        dashboard_service.py), which reads the shared SQLiteActivityStore and
        pushes the result here instead of calling record() per entry -- one
        publish per poll tick, not one per row.
        """
        with self._lock:
            self._entries = OrderedDict((entry.id, entry) for entry in reversed(entries))
            snapshot = self._snapshot_locked()
            self._subscribers.publish(snapshot)

    def subscribe(self) -> Queue[dict[str, Any]]:
        return self._subscribers.subscribe()

    def unsubscribe(self, subscriber: Queue[dict[str, Any]]) -> None:
        self._subscribers.unsubscribe(subscriber)

    @property
    def subscriber_count(self) -> int:
        return self._subscribers.count

    def _snapshot_locked(self) -> dict[str, list[dict[str, str | float]]]:
        return {
            "tasks": [
                {
                    "id": entry.id,
                    "agent": entry.agent,
                    "kind": entry.kind,
                    "state": entry.state,
                    "text": entry.text,
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                }
                for entry in reversed(self._entries.values())
            ]
        }
