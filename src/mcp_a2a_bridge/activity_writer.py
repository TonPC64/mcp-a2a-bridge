"""Opt-in, failure-isolated activity writes for local A2A executors."""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from mcp_a2a_bridge.activity import TEXT_PREVIEW_LIMIT
from mcp_a2a_bridge.activity_store import SQLiteActivityStore, resolve_activity_db_path

PEER_LIMIT = 256
STORE_RETRY_COOLDOWN = 30.0


def _bounded(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.replace("\x00", "").strip()
    return value[:PEER_LIMIT] or fallback


class ActivityWriter:
    """Best-effort shared-store writer; activity must never affect A2A work."""

    def __init__(self, destination: str, store: SQLiteActivityStore) -> None:
        self.destination = _bounded(destination, "local-agent")
        self.default_source = _bounded(os.environ.get("A2A_BRIDGE_ACTIVITY_SOURCE"), "remote")
        self._store = store
        self._paused_until = 0.0
        self._failure_reported = False

    def source_for(self, context: Any) -> str:
        """Use a verified authenticated name only; anonymous peers are remote."""
        try:
            user = context.call_context.user
            if user.is_authenticated:
                return _bounded(user.user_name, self.default_source)
        except Exception:
            pass
        return self.default_source

    def record(self, task_id: str, *, source: str, state: str, text: str) -> None:
        if time.time() < self._paused_until:
            return
        try:
            now = time.time()
            self._store.upsert(
                {
                    "id": _bounded(task_id, "unknown-task"),
                    "agent": self.destination,  # legacy API field
                    "source": _bounded(source, self.default_source),
                    "destination": self.destination,
                    "kind": "a2a_receive",
                    "state": _bounded(state, "working"),
                    "text": str(text).replace("\x00", "")[:TEXT_PREVIEW_LIMIT],
                    "created_at": now,
                    "updated_at": now,
                }
            )
            self._failure_reported = False
        except Exception as exc:  # activity must never break a request
            self._paused_until = time.time() + STORE_RETRY_COOLDOWN
            if not self._failure_reported:
                self._failure_reported = True
                print(f"mcp-a2a-bridge: local activity write failed: {exc}", file=sys.stderr)


def build_activity_writer(destination: str) -> ActivityWriter | None:
    """Return a writer only when the existing dashboard opt-in is enabled."""
    if os.environ.get("A2A_BRIDGE_DASHBOARD", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        return ActivityWriter(destination, SQLiteActivityStore(resolve_activity_db_path()))
    except Exception as exc:
        print(f"mcp-a2a-bridge: local activity disabled: {exc}", file=sys.stderr)
        return None
