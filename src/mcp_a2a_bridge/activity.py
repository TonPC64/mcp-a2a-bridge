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
STORE_RETRY_COOLDOWN = 30.0


@dataclass
class TaskActivity:
    id: str
    agent: str
    kind: str
    state: str
    text: str
    created_at: float
    updated_at: float
    source: str = ""
    destination: str = ""


class ActivityLog:
    """Bounded LRU log of task activity, keyed by task id.

    Mirrors the OrderedDict LRU shape of TTLTaskStore for consistency.
    """

    def __init__(
        self,
        maxsize: int = 500,
        store: SQLiteActivityStore | None = None,
        store_retry_cooldown: float = STORE_RETRY_COOLDOWN,
    ) -> None:
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
        self._store_retry_cooldown = store_retry_cooldown
        self._store_paused_until = 0.0
        self._store_failure_reported = False

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
            if self._store is not None and now >= self._store_paused_until:
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
                    self._store_failure_reported = False
                except Exception as exc:
                    # A crashed/slow/misconfigured shared store must never fail
                    # or slow down the real A2A tool call (spec "Error
                    # handling"). Pause writes for a cooldown instead of
                    # retrying immediately, so sustained contention doesn't
                    # re-pay the busy_timeout on every tool call -- but do not
                    # disable permanently, or one momentary lock contention
                    # would blind this process's activity for its whole
                    # lifetime. Warn once per outage to avoid flooding stderr,
                    # which is the bridge's only log channel.
                    # Anchor the cooldown to when the write actually failed,
                    # not to `now` (captured before the attempt): a contended
                    # write can itself burn the whole busy_timeout, which would
                    # otherwise leave the window already expired on return.
                    self._store_paused_until = time.time() + self._store_retry_cooldown
                    if not self._store_failure_reported:
                        self._store_failure_reported = True
                        print(
                            "mcp-a2a-bridge: shared activity store write failed, "
                            f"pausing shared-store writes for "
                            f"{self._store_retry_cooldown:g}s: {exc}",
                            file=sys.stderr,
                        )
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
                    **({"source": entry.source} if entry.source else {}),
                    **({"destination": entry.destination} if entry.destination else {}),
                }
                for entry in reversed(self._entries.values())
            ]
        }
