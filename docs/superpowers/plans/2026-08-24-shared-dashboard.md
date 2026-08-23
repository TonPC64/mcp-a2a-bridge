# Shared, Multi-Process Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard a single standalone process that shows live task activity from every `mcp-a2a-bridge` process on the machine, reachable from any device on the local network.

**Architecture:** Replace `ActivityLog`'s in-memory `OrderedDict` with a SQLite-backed store shared by all bridge processes. Remove the embedded per-bridge dashboard server entirely; add a new standalone `mcp-a2a-bridge-dashboard` console script that polls the shared SQLite file, diffs snapshots, and pushes SSE updates to any number of connected browsers. No auth token; dashboard binds `0.0.0.0` by default.

**Tech Stack:** Python 3.11+, `sqlite3` (stdlib), FastAPI, uvicorn, pytest/pytest-asyncio. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-24-shared-dashboard-design.md`

## Global Constraints

- No authentication/token — dashboard trusts the local network (explicit decision in spec).
- `A2A_BRIDGE_DASHBOARD` unset means `ActivityLog` stays in-memory only — zero SQLite file touched, no behavior change from today (spec "Non-goals"/"Components" section).
- Existing 500-entry TTL/LRU eviction bound is preserved (spec "Non-goals").
- Dashboard poll interval is 500ms (spec "Data flow" / "Components").
- Default DB path: `~/.config/a2a-bridge/activity.sqlite3`, override via `A2A_BRIDGE_ACTIVITY_DB` (spec "Components").
- Dashboard host default `0.0.0.0`, override via `A2A_BRIDGE_DASHBOARD_HOST`; port default `9100` via existing `A2A_BRIDGE_DASHBOARD_PORT` (spec "Components").
- SQLite errors on either side (bridge write, dashboard read) are caught, logged, never crash the stdio loop or the SSE stream (spec "Error handling").
- The embedded per-bridge dashboard code path (`start_dashboard`, `DashboardHandle`, FastAPI mount inside `server.py`) is deleted, not kept as a fallback (spec "Migration/compatibility notes").

---

## File Structure

- **Modify** `src/mcp_a2a_bridge/activity.py` — `ActivityLog` gains a pluggable storage backend. Keeps its public `record()`/`list()`/`subscribe()`/`unsubscribe()`/`subscriber_count` interface unchanged so `server.py`'s tool handlers need no changes.
- **Create** `src/mcp_a2a_bridge/activity_store.py` — `SQLiteActivityStore`, the persistent backend, following the exact pattern of `src/mcp_a2a_bridge/sqlite_task_store.py` (upsert-by-id, TTL+LRU eviction, WAL mode).
- **Create** `src/mcp_a2a_bridge/dashboard_service.py` — the standalone dashboard process: builds the FastAPI app via the existing `build_dashboard_app()`, owns the poll-diff-publish background task against `SQLiteActivityStore`, and exposes a `main()` entry point.
- **Modify** `src/mcp_a2a_bridge/server.py` — remove `start_dashboard()`, `DashboardHandle`, `_dashboard_enabled()`, and the dashboard-server wiring from `main()`. Add: when `A2A_BRIDGE_DASHBOARD=1`, construct `ActivityLog` with a `SQLiteActivityStore` backend pointed at `A2A_BRIDGE_ACTIVITY_DB` (or the default path); otherwise construct it with no backend (in-memory, today's default).
- **Modify** `pyproject.toml` — add `mcp-a2a-bridge-dashboard = "mcp_a2a_bridge.dashboard_service:main"` under `[project.scripts]`.
- **Modify** `tests/test_dashboard_startup.py` — delete (tests the removed embedded dashboard). Replaced by `tests/test_dashboard_service.py`.
- **Create** `tests/test_activity_store.py` — `SQLiteActivityStore` round-trip/eviction tests, and the cross-process regression test (two `ActivityLog` instances sharing one file).
- **Create** `tests/test_dashboard_service.py` — poll-diff-publish loop tests for the standalone service.
- **Modify** `tests/test_activity.py` — no signature changes needed; existing tests continue to exercise the default in-memory backend unchanged.
- **Modify** `README.md` — replace the "Dashboard" section per the spec's migration notes.

---

## Task 1: `SQLiteActivityStore` backend

**Files:**
- Create: `src/mcp_a2a_bridge/activity_store.py`
- Test: `tests/test_activity_store.py`

**Interfaces:**
- Consumes: nothing new (stdlib `sqlite3`, `pathlib.Path`, `time`, `threading`).
- Produces: `SQLiteActivityStore(path: Path, maxsize: int = 500)` with methods:
  - `def upsert(self, entry: dict) -> None` — `entry` has keys `id, agent, kind, state, text, created_at, updated_at` (matches `TaskActivity` field names/types exactly: all `str` except `created_at`/`updated_at` which are `float`).
  - `def list(self) -> list[dict]` — same dict shape as `upsert`, ordered newest-updated-first (matches `ActivityLog.list()`'s current reverse-insertion-order contract, but keyed on `updated_at DESC` since SQLite has no insertion-order guarantee across processes).
  - `def get(self, entry_id: str) -> dict | None`.
  - Module-level `DEFAULT_ACTIVITY_DB: Path` (`~/.config/a2a-bridge/activity.sqlite3`) and `def resolve_activity_db_path() -> Path` (reads `A2A_BRIDGE_ACTIVITY_DB`, falls back to `DEFAULT_ACTIVITY_DB`) — the single source of truth for this path, imported by both `dashboard_service.py` (Task 3) and `server.py` (Task 4) so the default/override logic is defined exactly once.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_activity_store.py
import time

from mcp_a2a_bridge.activity_store import SQLiteActivityStore


def _entry(entry_id: str, updated_at: float, **overrides) -> dict:
    base = {
        "id": entry_id,
        "agent": "planner",
        "kind": "send_message",
        "state": "working",
        "text": "hi",
        "created_at": updated_at,
        "updated_at": updated_at,
    }
    base.update(overrides)
    return base


def test_upsert_then_get_round_trips(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    store.upsert(_entry("t1", 100.0))

    got = store.get("t1")

    assert got == _entry("t1", 100.0)


def test_upsert_same_id_replaces_state_but_keeps_created_at(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    store.upsert(_entry("t1", 100.0, state="working"))
    store.upsert(_entry("t1", 200.0, state="completed", created_at=100.0))

    got = store.get("t1")

    assert got["state"] == "completed"
    assert got["created_at"] == 100.0
    assert got["updated_at"] == 200.0


def test_list_orders_newest_updated_first(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    store.upsert(_entry("t1", 100.0))
    store.upsert(_entry("t2", 200.0))

    assert [e["id"] for e in store.list()] == ["t2", "t1"]


def test_eviction_drops_oldest_updated_when_over_maxsize(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3", maxsize=1)
    store.upsert(_entry("t1", 100.0))
    store.upsert(_entry("t2", 200.0))

    assert [e["id"] for e in store.list()] == ["t2"]


def test_second_instance_sees_first_instances_writes(tmp_path):
    """Simulates two separate bridge processes sharing one SQLite file."""
    path = tmp_path / "activity.sqlite3"
    first = SQLiteActivityStore(path)
    first.upsert(_entry("t1", time.time()))

    second = SQLiteActivityStore(path)

    assert [e["id"] for e in second.list()] == ["t1"]


def test_resolve_activity_db_path_uses_env_override(monkeypatch, tmp_path):
    from mcp_a2a_bridge.activity_store import resolve_activity_db_path

    override = tmp_path / "custom.sqlite3"
    monkeypatch.setenv("A2A_BRIDGE_ACTIVITY_DB", str(override))

    assert resolve_activity_db_path() == override


def test_resolve_activity_db_path_defaults_when_unset(monkeypatch):
    from mcp_a2a_bridge.activity_store import DEFAULT_ACTIVITY_DB, resolve_activity_db_path

    monkeypatch.delenv("A2A_BRIDGE_ACTIVITY_DB", raising=False)

    assert resolve_activity_db_path() == DEFAULT_ACTIVITY_DB
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_a2a_bridge.activity_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/mcp_a2a_bridge/activity_store.py
"""SQLite-backed shared activity storage for the multi-process dashboard."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_ACTIVITY_DB = Path.home() / ".config" / "a2a-bridge" / "activity.sqlite3"


def resolve_activity_db_path() -> Path:
    """Where every bridge process writes to and the dashboard reads from.

    Single source of truth for A2A_BRIDGE_ACTIVITY_DB resolution -- imported
    by both dashboard_service.py and server.py so the default path can never
    drift between the writer side and the reader side.
    """
    override = os.environ.get("A2A_BRIDGE_ACTIVITY_DB")
    return Path(override).expanduser() if override else DEFAULT_ACTIVITY_DB


class SQLiteActivityStore:
    """Persist task activity entries so multiple bridge processes can share one log.

    Mirrors the upsert-by-id, TTL/LRU-eviction shape of
    ``mcp_a2a_bridge.sqlite_task_store.SQLiteTaskStore``. WAL mode is enabled
    so one bridge's write does not block another bridge's write, or the
    dashboard's concurrent reads.
    """

    def __init__(self, path: Path, maxsize: int = 500) -> None:
        self._path = Path(path).expanduser()
        self._maxsize = maxsize
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activity (
                    id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _evict(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM activity WHERE id IN (
                SELECT id FROM activity
                ORDER BY updated_at ASC
                LIMIT MAX(0, (SELECT COUNT(*) FROM activity) - ?)
            )
            """,
            (self._maxsize,),
        )

    def upsert(self, entry: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO activity(id, agent, kind, state, text, created_at, updated_at)
                VALUES (:id, :agent, :kind, :state, :text, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    agent=excluded.agent,
                    kind=excluded.kind,
                    state=excluded.state,
                    text=excluded.text,
                    updated_at=excluded.updated_at
                """,
                entry,
            )
            self._evict(connection)
            connection.commit()

    def get(self, entry_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, agent, kind, state, text, created_at, updated_at "
                "FROM activity WHERE id = ?",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, agent, kind, state, text, created_at, updated_at "
                "FROM activity ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        keys = ("id", "agent", "kind", "state", "text", "created_at", "updated_at")
        return dict(zip(keys, row))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/activity_store.py tests/test_activity_store.py
git commit -m "feat(dashboard): add SQLiteActivityStore for shared activity" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: Wire `ActivityLog` to an optional SQLite-backed store

**Files:**
- Modify: `src/mcp_a2a_bridge/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: `SQLiteActivityStore` from Task 1 (`upsert(entry: dict)`, `list() -> list[dict]`).
- Produces: `ActivityLog(maxsize: int = 500, store: SQLiteActivityStore | None = None)`. When `store` is `None` (today's default), behavior is 100% unchanged (in-memory `OrderedDict`, no SQLite touched). When `store` is provided, `record()` writes through to `store.upsert(...)` in addition to updating the in-memory dict (kept for the fast-path `list()`/SSE snapshot without a SQLite read on every call), and `list()` continues to read from the in-memory dict — cross-process visibility is achieved by the *dashboard service* (Task 3) polling the store directly, not by every `ActivityLog.list()` call hitting SQLite.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_activity.py

from mcp_a2a_bridge.activity_store import SQLiteActivityStore


async def test_record_writes_through_to_store_when_configured(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    log = ActivityLog(store=store)

    await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )

    stored = store.get("t1")
    assert stored is not None
    assert stored["agent"] == "planner"
    assert stored["state"] == "working"
    assert stored["text"] == "hi"


async def test_record_without_store_touches_no_sqlite_file(tmp_path):
    log = ActivityLog()  # store=None, today's default

    await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )

    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity.py -v -k store`
Expected: FAIL with `TypeError: ActivityLog.__init__() got an unexpected keyword argument 'store'`

- [ ] **Step 3: Write minimal implementation**

Modify `src/mcp_a2a_bridge/activity.py`:

```python
# Add import near the top, alongside the existing snapshots import:
from mcp_a2a_bridge.activity_store import SQLiteActivityStore

# Change __init__ signature and body:
    def __init__(self, maxsize: int = 500, store: SQLiteActivityStore | None = None) -> None:
        self._maxsize = maxsize
        self._entries: OrderedDict[str, TaskActivity] = OrderedDict()
        self._lock = threading.Lock()
        self._subscribers = SnapshotSubscribers()
        self._store = store

# In record(), after building `entry` and before `self._entries[key] = entry`,
# add the write-through (still inside the `with self._lock:` block, matching
# the file's existing critical-section shape):
            self._entries[key] = entry
            if self._store is not None:
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
            snapshot = self._snapshot_locked()
```

(The `store.upsert()` call is a plain synchronous SQLite call — same as the existing lock-held synchronous work in this method, so it does not change the "never await inside the critical section" invariant documented on `self._lock`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/activity.py tests/test_activity.py
git commit -m "feat(dashboard): write ActivityLog entries through to shared store" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: Standalone dashboard service with poll-diff-publish

**Files:**
- Create: `src/mcp_a2a_bridge/dashboard_service.py`
- Test: `tests/test_dashboard_service.py`

**Interfaces:**
- Consumes: `SQLiteActivityStore.list() -> list[dict]` (Task 1), `AgentRegistry` (existing, unchanged), `build_dashboard_app(registry, activity, dist_dir=None)` (existing, unchanged — the dashboard service still uses `ActivityLog` internally as the object it publishes into for the existing `/api/tasks` and `/api/tasks/events` handlers, just fed by the poll loop instead of by `record()` calls from tool handlers).
- Produces:
  - `build_poll_task(store: SQLiteActivityStore, activity: ActivityLog, interval_s: float = 0.5) -> Callable[[], Awaitable[None]]` — returns an async function that, when awaited in a loop, does one poll-diff-publish cycle: reads `store.list()`, compares against the last published snapshot (plain dict equality), and if different, calls `activity._subscribers.publish({"tasks": rows})` and replaces every entry in `activity`'s public view. To keep this simple and avoid reaching into `ActivityLog` internals, the poll task instead calls a new `ActivityLog.replace_all(entries: list[TaskActivity]) -> None` method (added in this task) that atomically swaps the in-memory `OrderedDict` and publishes a snapshot — reusing the exact publish path `record()` already uses.
  - `def main() -> None` — the `mcp-a2a-bridge-dashboard` console script entry point: loads the registry (same `load_registry(resolve_config_path())` as `server.py`), builds a `SQLiteActivityStore` at `A2A_BRIDGE_ACTIVITY_DB` or the default path, builds an `ActivityLog(store=None)` (the dashboard process itself never writes to the store, only reads — passing `store=None` here is correct and intentional), builds the FastAPI app via `build_dashboard_app`, starts the poll loop as a background `asyncio` task, and runs uvicorn on `A2A_BRIDGE_DASHBOARD_HOST` (default `0.0.0.0`) / `A2A_BRIDGE_DASHBOARD_PORT` (default `9100`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_service.py
import asyncio

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.activity_store import SQLiteActivityStore
from mcp_a2a_bridge.dashboard_service import build_poll_task


async def test_poll_task_publishes_new_snapshot_from_store(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    activity = ActivityLog()
    poll_once = build_poll_task(store, activity)

    store.upsert(
        {
            "id": "t1",
            "agent": "planner",
            "kind": "send_message",
            "state": "working",
            "text": "hi",
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    )
    await poll_once()

    entries = await activity.list()
    assert len(entries) == 1
    assert entries[0].id == "t1"
    assert entries[0].state == "working"


async def test_poll_task_does_not_publish_when_snapshot_unchanged():
    store = SQLiteActivityStoreStub()
    activity = ActivityLog()
    poll_once = build_poll_task(store, activity)

    subscriber = activity.subscribe()
    await poll_once()  # empty store -> empty snapshot, first publish
    first = subscriber.get(timeout=1)
    assert first == {"tasks": []}

    await poll_once()  # still empty -> must NOT publish again

    assert subscriber.empty()
    activity.unsubscribe(subscriber)


class SQLiteActivityStoreStub:
    """Minimal stand-in with an always-empty list(), for the no-op-publish test."""

    def list(self) -> list[dict]:
        return []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_a2a_bridge.dashboard_service'`

- [ ] **Step 3: Write minimal implementation**

First, add `replace_all` to `src/mcp_a2a_bridge/activity.py` (same file touched in Task 2):

```python
# Add to ActivityLog, near record()/list():
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
```

(`entries` arrives newest-first from `SQLiteActivityStore.list()`; reversing before inserting keeps `OrderedDict`'s insertion order consistent with the rest of the class, where `_snapshot_locked()` re-reverses for newest-first output — matching the existing convention used by `record()`.)

Then create the service:

```python
# src/mcp_a2a_bridge/dashboard_service.py
"""Standalone dashboard process: one long-lived service showing live task
activity from every mcp-a2a-bridge process on the machine.

Run via the ``mcp-a2a-bridge-dashboard`` console script. Independent of any
bridge process -- it can be started before or after any bridge, and stays up
across bridge restarts.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

import uvicorn

from mcp_a2a_bridge.activity import ActivityLog, TaskActivity
from mcp_a2a_bridge.activity_store import SQLiteActivityStore, resolve_activity_db_path
from mcp_a2a_bridge.config import ConfigError, load_registry, resolve_config_path
from mcp_a2a_bridge.dashboard import build_dashboard_app
from mcp_a2a_bridge.registry import AgentRegistry

DEFAULT_DASHBOARD_HOST = "0.0.0.0"
DEFAULT_DASHBOARD_PORT = 9100
DEFAULT_POLL_INTERVAL_S = 0.5


def build_poll_task(
    store: SQLiteActivityStore,
    activity: ActivityLog,
    interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> Callable[[], Awaitable[None]]:
    """Return a coroutine that does ONE poll-diff-publish cycle.

    ``interval_s`` is accepted so ``main()`` can drive this in a
    ``while True: await poll_once(); await asyncio.sleep(interval_s)`` loop; the
    tests call ``poll_once()`` directly without the sleep.
    """
    last_snapshot: dict | None = None

    async def poll_once() -> None:
        nonlocal last_snapshot
        try:
            rows = store.list()
        except Exception as exc:  # SQLite read failures must never crash the poll loop
            print(f"mcp-a2a-bridge-dashboard: poll failed: {exc}", file=sys.stderr)
            return

        snapshot = {"tasks": rows}
        if snapshot == last_snapshot:
            return
        last_snapshot = snapshot

        entries = [
            TaskActivity(
                id=row["id"],
                agent=row["agent"],
                kind=row["kind"],
                state=row["state"],
                text=row["text"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        await activity.replace_all(entries)

    return poll_once


async def _run_poll_loop(poll_once: Callable[[], Awaitable[None]], interval_s: float) -> None:
    while True:
        await poll_once()
        await asyncio.sleep(interval_s)


def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge-dashboard: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    store = SQLiteActivityStore(resolve_activity_db_path())
    activity = ActivityLog()
    app = build_dashboard_app(registry, activity)

    host = os.environ.get("A2A_BRIDGE_DASHBOARD_HOST", DEFAULT_DASHBOARD_HOST)
    port = int(os.environ.get("A2A_BRIDGE_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT))

    @app.on_event("startup")
    async def _start_poll_loop() -> None:
        poll_once = build_poll_task(store, activity)
        asyncio.create_task(_run_poll_loop(poll_once, DEFAULT_POLL_INTERVAL_S))

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity.py tests/test_dashboard_service.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/activity.py src/mcp_a2a_bridge/dashboard_service.py tests/test_dashboard_service.py
git commit -m "feat(dashboard): add standalone dashboard service with poll-diff-publish" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 4: Remove embedded per-bridge dashboard; wire bridges to the shared store

**Files:**
- Modify: `src/mcp_a2a_bridge/server.py`
- Delete: `tests/test_dashboard_startup.py`
- Test: `tests/test_server.py` (extend)

**Interfaces:**
- Consumes: `SQLiteActivityStore` (Task 1), `ActivityLog(store=...)` (Task 2).
- Produces: `main()` in `server.py` no longer starts an HTTP server; it builds `ActivityLog` with a `SQLiteActivityStore` backend when `A2A_BRIDGE_DASHBOARD=1`, else with no backend.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_server.py

from mcp_a2a_bridge.activity_store import SQLiteActivityStore
from mcp_a2a_bridge.server import build_activity_log


def test_build_activity_log_uses_sqlite_store_when_enabled(monkeypatch, tmp_path):
    db_path = tmp_path / "activity.sqlite3"
    monkeypatch.setenv("A2A_BRIDGE_DASHBOARD", "1")
    monkeypatch.setenv("A2A_BRIDGE_ACTIVITY_DB", str(db_path))

    log = build_activity_log()

    assert isinstance(log._store, SQLiteActivityStore)
    assert db_path.parent.is_dir()  # SQLiteActivityStore creates parent dirs eagerly


def test_build_activity_log_is_in_memory_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("A2A_BRIDGE_DASHBOARD", raising=False)
    monkeypatch.setenv("A2A_BRIDGE_ACTIVITY_DB", str(tmp_path / "should-not-be-created.sqlite3"))

    log = build_activity_log()

    assert log._store is None
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -v -k build_activity_log`
Expected: FAIL with `ImportError: cannot import name 'build_activity_log' from 'mcp_a2a_bridge.server'`

- [ ] **Step 3: Write minimal implementation**

In `src/mcp_a2a_bridge/server.py`:

1. Remove: the `import threading`, `import uvicorn` lines (no longer needed), the `from mcp_a2a_bridge.dashboard import build_dashboard_app` import, the `DashboardHandle` dataclass, `_dashboard_enabled()`, `start_dashboard()`, and the `start_dashboard(registry, activity)` call inside `main()`.
2. Add:

```python
from mcp_a2a_bridge.activity_store import SQLiteActivityStore, resolve_activity_db_path


def _dashboard_enabled() -> bool:
    return os.environ.get("A2A_BRIDGE_DASHBOARD", "").strip().lower() in {"1", "true", "yes", "on"}


def build_activity_log() -> ActivityLog:
    """Build the bridge's ActivityLog, backed by the shared SQLite store when
    A2A_BRIDGE_DASHBOARD=1 so the standalone mcp-a2a-bridge-dashboard process
    (see dashboard_service.py) can see this process's activity. Unset (the
    default) keeps activity in-memory only -- no SQLite file is touched.
    """
    if not _dashboard_enabled():
        return ActivityLog()
    return ActivityLog(store=SQLiteActivityStore(resolve_activity_db_path()))
```

3. Change `main()`:

```python
def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    activity = build_activity_log()
    build_server(registry, activity).run(transport="stdio")
```

- [ ] **Step 4: Delete the obsolete test file and run the suite**

```bash
rm tests/test_dashboard_startup.py
```

Run: `.venv/bin/pytest tests/test_server.py tests/test_activity.py tests/test_activity_store.py tests/test_dashboard_service.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full suite to confirm no other regressions**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tests; `test_dashboard.py`'s existing SSE contract tests still pass unchanged since `build_dashboard_app` itself was not touched)

- [ ] **Step 6: Commit**

```bash
git add src/mcp_a2a_bridge/server.py tests/test_server.py
git rm tests/test_dashboard_startup.py
git commit -m "feat(dashboard): remove embedded per-bridge dashboard, use shared store" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 5: Register the `mcp-a2a-bridge-dashboard` console script and update README

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `mcp_a2a_bridge.dashboard_service:main` (Task 3).
- Produces: a `mcp-a2a-bridge-dashboard` executable after `pip install`/`uv sync`.

- [ ] **Step 1: Add the console script**

In `pyproject.toml`, under `[project.scripts]`:

```toml
[project.scripts]
mcp-a2a-bridge = "mcp_a2a_bridge.server:main"
mcp-a2a-bridge-dashboard = "mcp_a2a_bridge.dashboard_service:main"
```

- [ ] **Step 2: Verify the script resolves**

Run: `cd /Users/chanwit/WorkSpace/mcp-a2a-bridge && uv build --wheel --out-dir /tmp/mcp-a2a-verify && unzip -p /tmp/mcp-a2a-verify/*.whl mcp_a2a_bridge-0.1.0.dist-info/entry_points.txt`
Expected: output includes `mcp-a2a-bridge-dashboard = mcp_a2a_bridge.dashboard_service:main`

- [ ] **Step 3: Replace the README "Dashboard" section**

Replace the section from `## Dashboard` up to (not including) `## Development` in `README.md` with:

```markdown
## Dashboard

A standalone, read-only web dashboard shows configured agents' status/skills
and a live, rolling history of task activity from *every* `mcp-a2a-bridge`
process on the machine — Copilot's, Hermes', or any other MCP host's. It runs
as its own long-lived process, independent of any bridge, so multiple devices
on the same local network can view it at once. It uses Server-Sent Events
(SSE) to push updates: `/api/agents/events` emits `agents` events with
`{ "agents": [...] }`, and `/api/tasks/events` emits `tasks` events with
`{ "tasks": [...] }`.

Build the frontend once:

    cd dashboard
    npm install
    npm run build

Start the dashboard (once, independent of any bridge):

    mcp-a2a-bridge-dashboard

Then enable each bridge process to report into the shared activity store:

    A2A_BRIDGE_DASHBOARD=1 mcp-a2a-bridge

| Env var | Default | Purpose |
|---|---|---|
| `A2A_BRIDGE_DASHBOARD` | unset (off) | Set to `1` on a *bridge* process to persist its activity into the shared SQLite store |
| `A2A_BRIDGE_ACTIVITY_DB` | `~/.config/a2a-bridge/activity.sqlite3` | Path to the shared activity store, read by the dashboard process and written by every enabled bridge process |
| `A2A_BRIDGE_DASHBOARD_HOST` | `0.0.0.0` | Host the dashboard HTTP server binds to |
| `A2A_BRIDGE_DASHBOARD_PORT` | `9100` | Port for the dashboard HTTP server |

Visit `http://<this-machine's-IP>:9100` from any device on the same local
network. The dashboard is read-only — it never sends messages to agents.

There is no authentication: this is a local-network-trust tool, like a Vite
dev server. Do not expose it beyond a trusted LAN.

Task activity is live across every bridge process that has
`A2A_BRIDGE_DASHBOARD=1` set, because they all write into the same
`A2A_BRIDGE_ACTIVITY_DB` file and the dashboard process polls it continuously.
A bridge process without the flag set keeps its activity in memory only and
is invisible to the dashboard (today's default, zero overhead).
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml README.md
git commit -m "docs(dashboard): document standalone dashboard service" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 6: End-to-end manual verification (no code changes)

**Files:** none (verification only)

- [ ] **Step 1: Build the frontend and install the package locally**

```bash
cd /Users/chanwit/WorkSpace/mcp-a2a-bridge/dashboard && npm install && npm run build
cd /Users/chanwit/WorkSpace/mcp-a2a-bridge && uv build --wheel --out-dir /tmp/mcp-a2a-verify
```

- [ ] **Step 2: Start the standalone dashboard**

```bash
A2A_BRIDGE_ACTIVITY_DB=/tmp/verify-activity.sqlite3 mcp-a2a-bridge-dashboard &
```

Expected: process starts, `curl http://127.0.0.1:9100/` returns 200 HTML.

- [ ] **Step 3: Start two separate bridge processes pointed at the same store**

```bash
A2A_BRIDGE_DASHBOARD=1 A2A_BRIDGE_ACTIVITY_DB=/tmp/verify-activity.sqlite3 mcp-a2a-bridge &
A2A_BRIDGE_DASHBOARD=1 A2A_BRIDGE_ACTIVITY_DB=/tmp/verify-activity.sqlite3 mcp-a2a-bridge &
```

(These run over stdio and won't do anything without an MCP client driving them, but this confirms neither process crashes on startup with the new `build_activity_log()` wiring — check `jobs` / process list stays alive for a few seconds.)

- [ ] **Step 4: Confirm dashboard reachability from a second device**

From another device on the same LAN, browse to `http://<this-machine's-LAN-IP>:9100/` and confirm the page loads (proves the `0.0.0.0` bind works, not just `127.0.0.1`).

- [ ] **Step 5: Clean up**

```bash
kill %1 %2 %3 2>/dev/null
rm -f /tmp/verify-activity.sqlite3
```

No commit for this task — it is manual verification only.
