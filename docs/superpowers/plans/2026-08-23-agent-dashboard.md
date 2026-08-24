# Agent Dashboard Implementation Plan

> **Status (2026-08-25): Superseded.** The embedded, `127.0.0.1` dashboard
> described here was replaced by the standalone shared-dashboard design in
> `2026-08-24-shared-dashboard.md` (see commit `3000ee1`). Keep this plan as
> historical context; do not backfill its unrecorded RED-phase checkboxes.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, read-only web dashboard showing configured A2A agents' status/skills and a rolling history of tasks the bridge has sent/polled/canceled.

**Architecture:** A bounded in-memory `ActivityLog` records every MCP tool call the bridge makes to agents. A FastAPI app (`dashboard.py`) exposes that log plus agent status as `/api/agents` and `/api/tasks`, and serves a built React SPA as static files. `server.py`'s `main()` starts this FastAPI app on a background uvicorn thread only when `A2A_BRIDGE_DASHBOARD` is set, alongside the existing stdio MCP server, which is otherwise unchanged.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, pytest/pytest-asyncio (backend); Vite, React 19, TypeScript, Vitest, React Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-08-23-agent-dashboard-design.md`

## Global Constraints

- Dashboard is off by default; enabled only when `A2A_BRIDGE_DASHBOARD` is set to one of `1`, `true`, `yes`, `on` (case-insensitive).
- Dashboard port comes from `A2A_BRIDGE_DASHBOARD_PORT`, default `9100`.
- Dashboard binds to `127.0.0.1` only — no authentication, matching the spec's local single-user scope.
- If the dashboard fails to start (e.g. port in use), log to stderr and continue running the stdio MCP server — the dashboard must never block or crash the primary bridge.
- `ActivityLog` is bounded to 500 entries (LRU eviction), text previews truncated to 500 characters, entries keyed by `task_id` (a generated `uuid4().hex` when the tool call has none).
- Dashboard is strictly read-only: no endpoint may send a message to an agent.
- Frontend: React 19 + Vite + TypeScript, polling `/api/agents` and `/api/tasks` every 3000 ms, no routing or state-management library, Vitest + React Testing Library for component/hook tests.

---

### Task 1: `ActivityLog` module

**Files:**
- Create: `src/mcp_a2a_bridge/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces: `TaskActivity` dataclass (`id: str`, `agent: str`, `kind: str`, `state: str`, `text: str`, `created_at: float`, `updated_at: float`); `ActivityLog(maxsize: int = 500)` with `async def record(self, *, task_id: str | None, agent: str, kind: str, state: str, text: str) -> TaskActivity` and `async def list(self) -> list[TaskActivity]` (newest first). `TEXT_PREVIEW_LIMIT = 500` constant.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_activity.py`:

```python
from mcp_a2a_bridge.activity import ActivityLog, TEXT_PREVIEW_LIMIT


async def test_record_without_task_id_generates_one():
    log = ActivityLog()
    entry = await log.record(
        task_id=None, agent="planner", kind="send_message", state="working", text="hi"
    )
    assert entry.id
    assert entry.agent == "planner"


async def test_repeat_task_id_updates_existing_entry_instead_of_duplicating():
    log = ActivityLog()
    first = await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )
    second = await log.record(
        task_id="t1", agent="planner", kind="get_task", state="completed", text="done"
    )

    entries = await log.list()
    assert len(entries) == 1
    assert entries[0].state == "completed"
    assert entries[0].kind == "get_task"
    assert entries[0].created_at == first.created_at
    assert entries[0].updated_at == second.updated_at


async def test_text_is_truncated_to_limit():
    log = ActivityLog()
    long_text = "x" * (TEXT_PREVIEW_LIMIT + 50)
    entry = await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text=long_text
    )
    assert len(entry.text) == TEXT_PREVIEW_LIMIT


async def test_list_returns_newest_first():
    log = ActivityLog()
    await log.record(task_id="t1", agent="a", kind="send_message", state="working", text="one")
    await log.record(task_id="t2", agent="a", kind="send_message", state="working", text="two")

    entries = await log.list()
    assert [e.id for e in entries] == ["t2", "t1"]


async def test_updating_an_entry_moves_it_to_newest():
    log = ActivityLog()
    await log.record(task_id="t1", agent="a", kind="send_message", state="working", text="one")
    await log.record(task_id="t2", agent="a", kind="send_message", state="working", text="two")
    await log.record(task_id="t1", agent="a", kind="get_task", state="completed", text="done")

    entries = await log.list()
    assert [e.id for e in entries] == ["t1", "t2"]


async def test_eviction_drops_oldest_when_full():
    log = ActivityLog(maxsize=1)
    await log.record(task_id="t1", agent="a", kind="send_message", state="working", text="one")
    await log.record(task_id="t2", agent="a", kind="send_message", state="working", text="two")

    entries = await log.list()
    assert [e.id for e in entries] == ["t2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_activity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_a2a_bridge.activity'`

- [ ] **Step 3: Implement `ActivityLog`**

Create `src/mcp_a2a_bridge/activity.py`:

```python
"""Bounded in-memory log of A2A task activity, for dashboard observability."""

from __future__ import annotations

import asyncio
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
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        task_id: str | None,
        agent: str,
        kind: str,
        state: str,
        text: str,
    ) -> TaskActivity:
        async with self._lock:
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
        async with self._lock:
            return list(reversed(self._entries.values()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_activity.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/activity.py tests/test_activity.py
git commit -m "feat(dashboard): add bounded ActivityLog for task history

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Wire `ActivityLog` into the MCP tools

**Files:**
- Modify: `src/mcp_a2a_bridge/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `ActivityLog`, `TaskActivity` from Task 1 (`src/mcp_a2a_bridge/activity.py`).
- Produces: `build_server(registry: AgentRegistry, activity: ActivityLog | None = None) -> MCPServer` (new optional second parameter; existing single-argument calls keep working).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server.py` (below the existing imports, add `from mcp_a2a_bridge.activity import ActivityLog` and `from mcp_a2a_bridge import client as client_module` and `from mcp_a2a_bridge.client import A2AResult`; append these tests at the end of the file):

```python
async def test_send_message_records_into_activity_log(monkeypatch):
    async def fake_send_message(entry, card, message, task_id=None, context_id=None, timeout_s=60):
        return A2AResult(state="completed", text="done", task_id="task-1", context_id="ctx-1", done=True)

    monkeypatch.setattr(client_module, "send_message", fake_send_message)

    activity = ActivityLog()
    server = build_server(fake_registry(planner="http://x"), activity=activity)

    await server.call_tool("a2a_send_message", {"agent": "planner", "message": "hi"})

    entries = await activity.list()
    assert len(entries) == 1
    assert entries[0].id == "task-1"
    assert entries[0].agent == "planner"
    assert entries[0].kind == "send_message"
    assert entries[0].state == "completed"
    assert entries[0].text == "done"


async def test_get_task_records_into_activity_log(monkeypatch):
    async def fake_get_task(entry, card, task_id):
        return A2AResult(state="working", text="still going", task_id=task_id, context_id=None, done=False)

    monkeypatch.setattr(client_module, "get_task", fake_get_task)

    activity = ActivityLog()
    server = build_server(fake_registry(planner="http://x"), activity=activity)

    await server.call_tool("a2a_get_task", {"agent": "planner", "task_id": "task-2"})

    entries = await activity.list()
    assert entries[0].id == "task-2"
    assert entries[0].kind == "get_task"
    assert entries[0].state == "working"


async def test_cancel_task_records_into_activity_log(monkeypatch):
    async def fake_cancel_task(entry, card, task_id):
        return A2AResult(state="canceled", text="", task_id=task_id, context_id=None, done=True)

    monkeypatch.setattr(client_module, "cancel_task", fake_cancel_task)

    activity = ActivityLog()
    server = build_server(fake_registry(planner="http://x"), activity=activity)

    await server.call_tool("a2a_cancel_task", {"agent": "planner", "task_id": "task-3"})

    entries = await activity.list()
    assert entries[0].id == "task-3"
    assert entries[0].kind == "cancel_task"
    assert entries[0].state == "canceled"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -v`
Expected: FAIL — `build_server() got an unexpected keyword argument 'activity'`

- [ ] **Step 3: Wire `ActivityLog` into `build_server`**

In `src/mcp_a2a_bridge/server.py`, add the import (with the other `mcp_a2a_bridge` imports):

```python
from mcp_a2a_bridge.activity import ActivityLog
```

Change the signature and body of `build_server`:

```python
def build_server(registry: AgentRegistry, activity: ActivityLog | None = None) -> MCPServer:
    server = MCPServer(name="a2a-bridge", instructions=INSTRUCTIONS)
    activity = activity if activity is not None else ActivityLog()
```

Update the three tools that talk to agents to record into `activity` after each call. `a2a_send_message`:

```python
    @server.tool()
    async def a2a_send_message(
        agent: str,
        message: str,
        task_id: str | None = None,
        context_id: str | None = None,
        timeout_s: int = 60,
    ) -> dict:
        """Send a message to an A2A agent and return its reply.

        Pass task_id to continue an existing task, for example to answer an
        agent that returned state="input_required". If the agent is still
        working when timeout_s elapses, this returns done=false with a task_id
        to poll rather than blocking.
        """
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.send_message(
            entry,
            card,
            message,
            task_id=task_id,
            context_id=context_id,
            timeout_s=timeout_s,
        )
        await activity.record(
            task_id=result.task_id,
            agent=agent,
            kind="send_message",
            state=result.state,
            text=result.text,
        )
        return result.to_dict()
```

`a2a_get_task`:

```python
    @server.tool()
    async def a2a_get_task(agent: str, task_id: str) -> dict:
        """Get the current state and output of a previously started A2A task."""
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.get_task(entry, card, task_id)
        await activity.record(
            task_id=result.task_id or task_id,
            agent=agent,
            kind="get_task",
            state=result.state,
            text=result.text,
        )
        return result.to_dict()
```

`a2a_cancel_task`:

```python
    @server.tool()
    async def a2a_cancel_task(agent: str, task_id: str) -> dict:
        """Cancel a running A2A task and return its final state."""
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.cancel_task(entry, card, task_id)
        await activity.record(
            task_id=result.task_id or task_id,
            agent=agent,
            kind="cancel_task",
            state=result.state,
            text=result.text,
        )
        return result.to_dict()
```

Leave `a2a_list_agents` and `a2a_add_agent` unchanged for this task (they don't send or poll tasks).

In `main()`, create the shared `ActivityLog` and pass it through:

```python
def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    activity = ActivityLog()
    build_server(registry, activity).run(transport="stdio")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_server.py tests/test_activity.py -v`
Expected: PASS (all tests, including the pre-existing five)

- [ ] **Step 5: Run the full test suite to check nothing else broke**

Run: `.venv/bin/pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/mcp_a2a_bridge/server.py tests/test_server.py
git commit -m "feat(dashboard): record task activity from MCP tool calls

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Dashboard FastAPI app (`/api/agents`, `/api/tasks`, static files)

**Files:**
- Modify: `src/mcp_a2a_bridge/registry.py` (extract shared summary helper)
- Modify: `src/mcp_a2a_bridge/server.py` (use the shared helper, no behavior change)
- Create: `src/mcp_a2a_bridge/dashboard.py`
- Modify: `pyproject.toml` (promote `fastapi` to a runtime dependency)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `AgentRegistry`, `ResolvedAgent` (Task 1's `registry.py`, unchanged); `ActivityLog` (Task 1).
- Produces: `registry.resolved_agent_summary(item: ResolvedAgent) -> dict`; `dashboard.build_dashboard_app(registry: AgentRegistry, activity: ActivityLog, dist_dir: Path | None = None) -> FastAPI`; `dashboard.DIST_DIR: Path` (default location `dashboard/dist` at the repo root).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard.py`:

```python
import asyncio

from a2a.types import AgentCard
from fastapi.testclient import TestClient

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.config import AgentEntry, Registry
from mcp_a2a_bridge.dashboard import build_dashboard_app
from mcp_a2a_bridge.registry import AgentRegistry


def fake_registry(**agents):
    async def fetch(entry):
        if entry.name == "bad":
            raise RuntimeError("refused")
        return AgentCard(name=entry.name, description="d", version="1.0.0")

    return AgentRegistry(
        Registry(
            path=None,
            agents={n: AgentEntry(name=n, url=u, headers={}) for n, u in agents.items()},
        ),
        fetch_card=fetch,
    )


def test_get_agents_returns_reachability_and_errors():
    registry = fake_registry(good="http://x", bad="http://y")
    app = build_dashboard_app(registry, ActivityLog())
    client = TestClient(app)

    response = client.get("/api/agents")

    assert response.status_code == 200
    agents = {a["name"]: a for a in response.json()["agents"]}
    assert agents["good"]["reachable"] is True
    assert agents["bad"]["reachable"] is False
    assert "refused" in agents["bad"]["error"]


def test_get_tasks_returns_recorded_activity():
    registry = fake_registry()
    activity = ActivityLog()
    asyncio.run(
        activity.record(
            task_id="t1", agent="planner", kind="send_message", state="completed", text="done"
        )
    )
    app = build_dashboard_app(registry, activity)
    client = TestClient(app)

    response = client.get("/api/tasks")

    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "t1"
    assert tasks[0]["agent"] == "planner"
    assert tasks[0]["kind"] == "send_message"
    assert tasks[0]["state"] == "completed"
    assert tasks[0]["text"] == "done"


def test_root_without_build_returns_helpful_404(tmp_path):
    registry = fake_registry()
    app = build_dashboard_app(registry, ActivityLog(), dist_dir=tmp_path / "missing")
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 404
    assert "not built" in response.json()["error"]


def test_root_with_build_serves_index_html(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>dashboard</body></html>")

    registry = fake_registry()
    app = build_dashboard_app(registry, ActivityLog(), dist_dir=dist)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "dashboard" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_a2a_bridge.dashboard'`

- [ ] **Step 3: Extract the shared agent-summary helper**

In `src/mcp_a2a_bridge/registry.py`, add the import and function (after the `ResolvedAgent` dataclass, before `AgentRegistry`):

```python
from mcp_a2a_bridge import client


def resolved_agent_summary(item: ResolvedAgent) -> dict:
    summary = {
        "name": item.entry.name,
        "configured_url": item.entry.url,
        "reachable": item.reachable,
    }
    if item.card is not None:
        summary.update(client.card_summary(item.card))
        summary["name"] = item.entry.name
    else:
        summary["error"] = item.error
    return summary
```

- [ ] **Step 4: Use the helper in `a2a_list_agents` (no behavior change)**

In `src/mcp_a2a_bridge/server.py`, change the import to `from mcp_a2a_bridge.registry import AgentRegistry, resolved_agent_summary` and replace the body of `a2a_list_agents`:

```python
    @server.tool()
    async def a2a_list_agents(refresh: bool = False) -> dict:
        """List configured A2A agents with their skills and reachability.

        Set refresh=true to re-fetch agent cards that were previously cached.
        """
        agents = [
            resolved_agent_summary(item) for item in await registry.resolve_all(refresh=refresh)
        ]
        return {
            "agents": agents,
            "config_path": str(registry.config_path) if registry.config_path else None,
        }
```

- [ ] **Step 5: Run `tests/test_server.py` to confirm the refactor is behavior-preserving**

Run: `.venv/bin/pytest tests/test_server.py tests/test_integration.py -v`
Expected: PASS (unchanged assertions on `a2a_list_agents` output)

- [ ] **Step 6: Promote `fastapi` to a runtime dependency**

In `pyproject.toml`, change:

```toml
dependencies = [
    "mcp>=2.0.0,<3",
    "a2a-sdk>=1.1,<2",
    "httpx>=0.28.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "a2a-sdk[http-server]>=1.1,<2",
    "fastapi>=0.115",
    "uvicorn>=0.30",
]
```

to:

```toml
dependencies = [
    "mcp>=2.0.0,<3",
    "a2a-sdk>=1.1,<2",
    "httpx>=0.28.1",
    "fastapi>=0.115",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "a2a-sdk[http-server]>=1.1,<2",
    "uvicorn>=0.30",
]
```

Run: `uv lock && uv pip install -e ".[dev]"`
Expected: lockfile updates and the editable install succeeds

- [ ] **Step 7: Implement `dashboard.py`**

Create `src/mcp_a2a_bridge/dashboard.py`:

```python
"""FastAPI app exposing agent status and task history for the dashboard UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.registry import AgentRegistry, resolved_agent_summary

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"


def build_dashboard_app(
    registry: AgentRegistry,
    activity: ActivityLog,
    dist_dir: Path | None = None,
) -> FastAPI:
    dist_dir = dist_dir if dist_dir is not None else DIST_DIR
    app = FastAPI(title="a2a-bridge dashboard")

    @app.get("/api/agents")
    async def get_agents() -> dict:
        agents = [
            resolved_agent_summary(item) for item in await registry.resolve_all(refresh=False)
        ]
        return {"agents": agents}

    @app.get("/api/tasks")
    async def get_tasks() -> dict:
        entries = await activity.list()
        return {
            "tasks": [
                {
                    "id": e.id,
                    "agent": e.agent,
                    "kind": e.kind,
                    "state": e.state,
                    "text": e.text,
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                }
                for e in entries
            ]
        }

    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="dashboard")
    else:

        @app.get("/")
        async def missing_build() -> JSONResponse:
            return JSONResponse(
                {"error": f"Dashboard frontend not built. Expected files in {dist_dir}."},
                status_code=404,
            )

    return app
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/mcp_a2a_bridge/registry.py src/mcp_a2a_bridge/server.py src/mcp_a2a_bridge/dashboard.py pyproject.toml uv.lock tests/test_dashboard.py
git commit -m "feat(dashboard): add FastAPI app for agent status and task history

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: Start the dashboard from `main()`

**Files:**
- Modify: `src/mcp_a2a_bridge/server.py`
- Modify: `pyproject.toml` (promote `uvicorn` to a runtime dependency)
- Test: `tests/test_dashboard_startup.py`

**Interfaces:**
- Consumes: `build_dashboard_app` (Task 3, `dashboard.py`); `ActivityLog` (Task 1).
- Produces: `DashboardHandle` dataclass (`thread: threading.Thread`, `server: uvicorn.Server`); `start_dashboard(registry: AgentRegistry, activity: ActivityLog) -> DashboardHandle | None` in `server.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_startup.py`:

```python
import socket
import time

import httpx
import pytest

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.config import AgentEntry, Registry
from mcp_a2a_bridge.registry import AgentRegistry
from mcp_a2a_bridge.server import start_dashboard


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fake_registry(**agents):
    async def fetch(entry):
        return None

    return AgentRegistry(
        Registry(
            path=None,
            agents={n: AgentEntry(name=n, url=u, headers={}) for n, u in agents.items()},
        ),
        fetch_card=fetch,
    )


def test_start_dashboard_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("A2A_BRIDGE_DASHBOARD", raising=False)
    handle = start_dashboard(fake_registry(), ActivityLog())
    assert handle is None


def test_start_dashboard_serves_api_when_enabled(monkeypatch):
    from a2a.types import AgentCard

    async def fetch(entry):
        return AgentCard(name=entry.name, description="d", version="1.0.0")

    registry = AgentRegistry(
        Registry(path=None, agents={"planner": AgentEntry(name="planner", url="http://x", headers={})}),
        fetch_card=fetch,
    )

    port = _free_port()
    monkeypatch.setenv("A2A_BRIDGE_DASHBOARD", "1")
    monkeypatch.setenv("A2A_BRIDGE_DASHBOARD_PORT", str(port))

    handle = start_dashboard(registry, ActivityLog())
    assert handle is not None

    deadline = time.time() + 10
    response = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/api/agents", timeout=1)
            if response.status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        pytest.fail("dashboard did not start")

    assert response.json()["agents"][0]["name"] == "planner"

    handle.server.should_exit = True
    handle.thread.join(timeout=10)
    assert not handle.thread.is_alive()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dashboard_startup.py -v`
Expected: FAIL with `ImportError: cannot import name 'start_dashboard'`

- [ ] **Step 3: Promote `uvicorn` to a runtime dependency**

In `pyproject.toml`, change:

```toml
dependencies = [
    "mcp>=2.0.0,<3",
    "a2a-sdk>=1.1,<2",
    "httpx>=0.28.1",
    "fastapi>=0.115",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "a2a-sdk[http-server]>=1.1,<2",
    "uvicorn>=0.30",
]
```

to:

```toml
dependencies = [
    "mcp>=2.0.0,<3",
    "a2a-sdk>=1.1,<2",
    "httpx>=0.28.1",
    "fastapi>=0.115",
    "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "a2a-sdk[http-server]>=1.1,<2",
]
```

Run: `uv lock && uv pip install -e ".[dev]"`
Expected: lockfile updates and the editable install succeeds

- [ ] **Step 4: Implement `start_dashboard` and wire it into `main()`**

In `src/mcp_a2a_bridge/server.py`, add imports (with the others):

```python
import os
import threading
from dataclasses import dataclass

import uvicorn

from mcp_a2a_bridge.dashboard import build_dashboard_app
```

Add near the top-level constants:

```python
DEFAULT_DASHBOARD_PORT = 9100


@dataclass
class DashboardHandle:
    thread: threading.Thread
    server: uvicorn.Server


def _dashboard_enabled() -> bool:
    return os.environ.get("A2A_BRIDGE_DASHBOARD", "").strip().lower() in {"1", "true", "yes", "on"}


def start_dashboard(registry: AgentRegistry, activity: ActivityLog) -> DashboardHandle | None:
    """Start the dashboard HTTP server on a daemon thread. Returns None if disabled.

    Any startup failure (e.g. the port is already in use) is logged to
    stderr and swallowed: the dashboard must never prevent the stdio MCP
    server from running.
    """
    if not _dashboard_enabled():
        return None

    port = int(os.environ.get("A2A_BRIDGE_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT))
    app = build_dashboard_app(registry, activity)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def run() -> None:
        try:
            server.run()
        except Exception as exc:
            print(f"mcp-a2a-bridge: dashboard failed to start: {exc}", file=sys.stderr)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return DashboardHandle(thread=thread, server=server)
```

Update `main()`:

```python
def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    activity = ActivityLog()
    start_dashboard(registry, activity)

    build_server(registry, activity).run(transport="stdio")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard_startup.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/mcp_a2a_bridge/server.py pyproject.toml uv.lock tests/test_dashboard_startup.py
git commit -m "feat(dashboard): start dashboard server from main() behind an env flag

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: Scaffold the Vite + React + TypeScript frontend

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/index.html`
- Create: `dashboard/src/main.tsx`
- Create: `dashboard/src/index.css`
- Create: `dashboard/src/setupTests.ts`
- Create: `dashboard/src/App.tsx`
- Create: `dashboard/src/App.test.tsx`
- Modify: `.gitignore`

**Interfaces:**
- Produces: a working `npm run build` (outputs `dashboard/dist/`) and `npm test` pipeline that later tasks add components/tests to.

- [ ] **Step 1: Create `dashboard/package.json`**

```json
{
  "name": "a2a-bridge-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^19.2.8",
    "react-dom": "^19.2.8"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^7.0.1",
    "@testing-library/react": "^16.3.2",
    "@types/react": "^19.2.18",
    "@types/react-dom": "^19.2.4",
    "@vitejs/plugin-react": "^6.1.0",
    "jsdom": "^30.0.1",
    "typescript": "^7.0.2",
    "vite": "^8.2.2",
    "vitest": "^4.1.11"
  }
}
```

- [ ] **Step 2: Create `dashboard/vite.config.ts`**

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:9100",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
  },
});
```

- [ ] **Step 3: Create `dashboard/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create `dashboard/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>A2A Bridge Dashboard</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `dashboard/src/main.tsx`**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

- [ ] **Step 6: Create `dashboard/src/index.css`**

```css
body {
  font-family: system-ui, sans-serif;
  margin: 2rem;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 2rem;
}

th,
td {
  border: 1px solid #ccc;
  padding: 0.5rem;
  text-align: left;
}

.badge {
  padding: 0.15rem 0.5rem;
  border-radius: 0.5rem;
  font-size: 0.85rem;
}

.badge-ok,
.badge-completed {
  background: #d1fae5;
  color: #065f46;
}

.badge-error,
.badge-failed {
  background: #fee2e2;
  color: #991b1b;
}

.error {
  color: #991b1b;
}
```

- [ ] **Step 7: Create `dashboard/src/setupTests.ts`**

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 8: Create `dashboard/src/App.tsx`**

```tsx
export default function App() {
  return <h1>A2A Bridge Dashboard</h1>;
}
```

- [ ] **Step 9: Create `dashboard/src/App.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders the dashboard heading", () => {
    render(<App />);
    expect(screen.getByText("A2A Bridge Dashboard")).toBeInTheDocument();
  });
});
```

- [ ] **Step 10: Install dependencies**

Run: `cd dashboard && npm install`
Expected: `node_modules/` created, `package-lock.json` written

- [ ] **Step 11: Run tests to verify they pass**

Run: `cd dashboard && npm test`
Expected: PASS (1 test)

- [ ] **Step 12: Run the build to verify it succeeds**

Run: `cd dashboard && npm run build`
Expected: `dashboard/dist/` created with `index.html` and bundled assets, no TypeScript errors

- [ ] **Step 13: Ignore generated frontend output**

Append to `.gitignore`:

```
dashboard/node_modules/
dashboard/dist/
```

- [ ] **Step 14: Commit**

```bash
git add dashboard/package.json dashboard/package-lock.json dashboard/vite.config.ts dashboard/tsconfig.json dashboard/index.html dashboard/src/main.tsx dashboard/src/index.css dashboard/src/setupTests.ts dashboard/src/App.tsx dashboard/src/App.test.tsx .gitignore
git commit -m "feat(dashboard): scaffold Vite + React + TypeScript frontend

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 6: `useApi` polling hook

**Files:**
- Create: `dashboard/src/useApi.ts`
- Test: `dashboard/src/useApi.test.ts`

**Interfaces:**
- Produces: `useApi<T>(path: string, intervalMs: number): { data: T | null; error: string | null; loading: boolean }`.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/useApi.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useApi } from "./useApi";

describe("useApi", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("fetches immediately and stores the parsed JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ hello: "world" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApi<{ hello: string }>("/api/agents", 3000));
    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual({ hello: "world" });
    expect(result.current.loading).toBe(false);
  });

  it("polls again after the interval elapses", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ hello: "world" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    renderHook(() => useApi("/api/agents", 3000));
    await act(async () => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops polling after unmount", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ hello: "world" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderHook(() => useApi("/api/agents", 3000));
    await act(async () => {});
    expect(fetchMock).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("captures a non-ok response as an error", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApi("/api/tasks", 3000));
    await act(async () => {});

    expect(result.current.error).toBe("/api/tasks returned 500");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test`
Expected: FAIL — `Cannot find module './useApi'`

- [ ] **Step 3: Implement `useApi`**

Create `dashboard/src/useApi.ts`:

```ts
import { useEffect, useState } from "react";

interface UseApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useApi<T>(path: string, intervalMs: number): UseApiState<T> {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;

    async function fetchOnce() {
      try {
        const response = await fetch(path);
        if (!response.ok) {
          throw new Error(`${path} returned ${response.status}`);
        }
        const data = (await response.json()) as T;
        if (!cancelled) {
          setState({ data, error: null, loading: false });
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          setState((prev) => ({ ...prev, error: message, loading: false }));
        }
      }
    }

    fetchOnce();
    const id = setInterval(fetchOnce, intervalMs);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [path, intervalMs]);

  return state;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npm test`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/useApi.ts dashboard/src/useApi.test.ts
git commit -m "feat(dashboard): add useApi polling hook

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 7: `AgentList` component

**Files:**
- Create: `dashboard/src/AgentList.tsx`
- Test: `dashboard/src/AgentList.test.tsx`

**Interfaces:**
- Produces: `Agent` interface and `AgentList({ agents: Agent[] })` component, consumed by `App.tsx` in Task 9.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/AgentList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentList, type Agent } from "./AgentList";

describe("AgentList", () => {
  it("shows a message when there are no agents", () => {
    render(<AgentList agents={[]} />);
    expect(screen.getByText("No agents configured.")).toBeInTheDocument();
  });

  it("renders reachable agents with their skills", () => {
    const agents: Agent[] = [
      {
        name: "planner",
        configured_url: "http://localhost:9001",
        reachable: true,
        skills: [{ id: "plan", name: "Planning", description: "d", tags: [], examples: [] }],
      },
    ];
    render(<AgentList agents={agents} />);

    expect(screen.getByText("planner")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
    expect(screen.getByText("Planning")).toBeInTheDocument();
  });

  it("renders unreachable agents with their error as a tooltip", () => {
    const agents: Agent[] = [
      {
        name: "broken",
        configured_url: "http://localhost:9002",
        reachable: false,
        error: "connection refused",
      },
    ];
    render(<AgentList agents={agents} />);

    const badge = screen.getByText("unreachable");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute("title", "connection refused");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test`
Expected: FAIL — `Cannot find module './AgentList'`

- [ ] **Step 3: Implement `AgentList`**

Create `dashboard/src/AgentList.tsx`:

```tsx
export interface AgentSkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
  examples: string[];
}

export interface Agent {
  name: string;
  configured_url: string;
  reachable: boolean;
  description?: string;
  version?: string;
  url?: string | null;
  streaming?: boolean;
  input_modes?: string[];
  output_modes?: string[];
  skills?: AgentSkill[];
  error?: string;
}

export function AgentList({ agents }: { agents: Agent[] }) {
  if (agents.length === 0) {
    return <p>No agents configured.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Status</th>
          <th>URL</th>
          <th>Skills</th>
        </tr>
      </thead>
      <tbody>
        {agents.map((agent) => (
          <tr key={agent.name}>
            <td>{agent.name}</td>
            <td>
              {agent.reachable ? (
                <span className="badge badge-ok">reachable</span>
              ) : (
                <span className="badge badge-error" title={agent.error}>
                  unreachable
                </span>
              )}
            </td>
            <td>{agent.configured_url}</td>
            <td>{(agent.skills ?? []).map((skill) => skill.name).join(", ") || "\u2014"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npm test`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/AgentList.tsx dashboard/src/AgentList.test.tsx
git commit -m "feat(dashboard): add AgentList component

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 8: `TaskList` component

**Files:**
- Create: `dashboard/src/TaskList.tsx`
- Test: `dashboard/src/TaskList.test.tsx`

**Interfaces:**
- Produces: `TaskActivity` interface and `TaskList({ tasks: TaskActivity[] })` component, consumed by `App.tsx` in Task 9.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/TaskList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskList, type TaskActivity } from "./TaskList";

describe("TaskList", () => {
  it("shows a message when there is no activity", () => {
    render(<TaskList tasks={[]} />);
    expect(screen.getByText("No task activity yet.")).toBeInTheDocument();
  });

  it("renders a task row with its state and text", () => {
    const tasks: TaskActivity[] = [
      {
        id: "12345678-abcd",
        agent: "planner",
        kind: "send_message",
        state: "completed",
        text: "done",
        created_at: 1700000000,
        updated_at: 1700000005,
      },
    ];
    render(<TaskList tasks={tasks} />);

    expect(screen.getByText("12345678")).toBeInTheDocument();
    expect(screen.getByText("planner")).toBeInTheDocument();
    expect(screen.getByText("send_message")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test`
Expected: FAIL — `Cannot find module './TaskList'`

- [ ] **Step 3: Implement `TaskList`**

Create `dashboard/src/TaskList.tsx`:

```tsx
export interface TaskActivity {
  id: string;
  agent: string;
  kind: string;
  state: string;
  text: string;
  created_at: number;
  updated_at: number;
}

function formatTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

export function TaskList({ tasks }: { tasks: TaskActivity[] }) {
  if (tasks.length === 0) {
    return <p>No task activity yet.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Task</th>
          <th>Agent</th>
          <th>Kind</th>
          <th>State</th>
          <th>Last update</th>
          <th>Text</th>
        </tr>
      </thead>
      <tbody>
        {tasks.map((task) => (
          <tr key={task.id}>
            <td>{task.id.slice(0, 8)}</td>
            <td>{task.agent}</td>
            <td>{task.kind}</td>
            <td>
              <span className={`badge badge-${task.state}`}>{task.state}</span>
            </td>
            <td>{formatTime(task.updated_at)}</td>
            <td>{task.text}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && npm test`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/TaskList.tsx dashboard/src/TaskList.test.tsx
git commit -m "feat(dashboard): add TaskList component

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 9: Wire `App`, build the frontend, verify it's served end to end, document

**Files:**
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/App.test.tsx`
- Modify: `README.md`

**Interfaces:**
- Consumes: `useApi` (Task 6), `Agent`/`AgentList` (Task 7), `TaskActivity`/`TaskList` (Task 8), `DIST_DIR`/`build_dashboard_app` (Task 3), `start_dashboard` (Task 4).

- [ ] **Step 1: Write the failing test for the wired `App`**

Replace the contents of `dashboard/src/App.test.tsx`:

```tsx
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the dashboard heading and both sections", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ agents: [], tasks: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await act(async () => {});

    expect(screen.getByText("A2A Bridge Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(screen.getByText("Task activity")).toBeInTheDocument();
    expect(screen.getByText("No agents configured.")).toBeInTheDocument();
    expect(screen.getByText("No task activity yet.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd dashboard && npm test`
Expected: FAIL — `App` still renders only the bare heading, so `screen.getByText("Agents")` etc. are not found

- [ ] **Step 3: Wire `App.tsx`**

Replace the contents of `dashboard/src/App.tsx`:

```tsx
import { AgentList, type Agent } from "./AgentList";
import { TaskList, type TaskActivity } from "./TaskList";
import { useApi } from "./useApi";

const POLL_INTERVAL_MS = 3000;

export default function App() {
  const agents = useApi<{ agents: Agent[] }>("/api/agents", POLL_INTERVAL_MS);
  const tasks = useApi<{ tasks: TaskActivity[] }>("/api/tasks", POLL_INTERVAL_MS);

  return (
    <main>
      <h1>A2A Bridge Dashboard</h1>

      <section>
        <h2>Agents</h2>
        {agents.error && <p className="error">{agents.error}</p>}
        <AgentList agents={agents.data?.agents ?? []} />
      </section>

      <section>
        <h2>Task activity</h2>
        {tasks.error && <p className="error">{tasks.error}</p>}
        <TaskList tasks={tasks.data?.tasks ?? []} />
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd dashboard && npm test`
Expected: PASS (all frontend tests)

- [ ] **Step 5: Build the frontend**

Run: `cd dashboard && npm run build`
Expected: `dashboard/dist/index.html` and bundled assets produced, no TypeScript errors

- [ ] **Step 6: Verify the backend serves the real build**

Run:

```bash
.venv/bin/python -c "
from fastapi.testclient import TestClient
from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.config import Registry
from mcp_a2a_bridge.dashboard import build_dashboard_app
from mcp_a2a_bridge.registry import AgentRegistry

registry = AgentRegistry(Registry(path=None, agents={}))
app = build_dashboard_app(registry, ActivityLog())
client = TestClient(app)
response = client.get('/')
print(response.status_code)
assert response.status_code == 200
assert 'A2A Bridge Dashboard' in response.text
print('OK: dashboard/dist is served at /')
"
```

Expected: prints `200` then `OK: dashboard/dist is served at /` (this exercises the real, un-overridden `DIST_DIR` now that Step 5 has built it)

- [ ] **Step 7: Run the full backend test suite once more**

Run: `.venv/bin/pytest -v`
Expected: PASS — confirms Task 3's `test_root_without_build_returns_helpful_404` / `test_root_with_build_serves_index_html` still pass because they inject their own `dist_dir` and are unaffected by the real build now existing on disk

- [ ] **Step 8: Document the dashboard in the README**

Add a new section to `README.md`, after the "## Tools" section and before "## Development":

```markdown
## Dashboard

An optional read-only web dashboard shows configured agents' status/skills
and a rolling history of tasks the bridge has sent, polled, or canceled.

Build the frontend once:

    cd dashboard
    npm install
    npm run build

Then enable the dashboard when running the bridge:

    A2A_BRIDGE_DASHBOARD=1 mcp-a2a-bridge

| Env var | Default | Purpose |
|---|---|---|
| `A2A_BRIDGE_DASHBOARD` | unset (off) | Set to `1` to start the dashboard HTTP server |
| `A2A_BRIDGE_DASHBOARD_PORT` | `9100` | Port for the dashboard HTTP server |

Visit `http://127.0.0.1:9100` (or your configured port). The dashboard is
read-only — it does not send messages to agents.
```

- [ ] **Step 9: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/App.test.tsx README.md
git commit -m "feat(dashboard): wire App to live agent/task data and document usage

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```
