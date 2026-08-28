import pytest
from a2a.helpers import new_text_message
from a2a.types import Role
from fastapi.testclient import TestClient

import run_copilot_main_dev as executor_mod
from run_copilot_main_dev import CopilotExecutor, CopilotResult
from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.activity_store import SQLiteActivityStore
from mcp_a2a_bridge.activity_writer import ActivityWriter
from mcp_a2a_bridge.config import Registry
from mcp_a2a_bridge.dashboard import build_dashboard_app
from mcp_a2a_bridge.dashboard_service import build_poll_task
from mcp_a2a_bridge.registry import AgentRegistry


class Queue:
    async def enqueue_event(self, event):
        pass


class Context:
    def __init__(self):
        self.message = new_text_message("do it", role=Role.ROLE_USER)
        self.current_task = None


@pytest.mark.anyio
async def test_copilot_executor_writes_shared_sqlite_and_dashboard_reads_it(monkeypatch, tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    writer = ActivityWriter("copilot", store)
    monkeypatch.setattr(executor_mod, "build_activity_writer", lambda destination: writer)

    async def run(*args, **kwargs):
        yield {"type": "result"}, CopilotResult(text="done", exit_code=0)

    monkeypatch.setattr(executor_mod, "run_copilot", run)
    await CopilotExecutor("/tmp").execute(Context(), Queue())

    activity = ActivityLog()
    await build_poll_task(store, activity, hermes_audit_path=tmp_path / "missing")()
    app = build_dashboard_app(AgentRegistry(Registry(path=None, agents={})), activity)
    task = TestClient(app).get("/api/tasks").json()["tasks"][0]

    assert task["destination"] == "copilot"
    assert task["source"] == "remote"
    assert task["state"] == "completed"


def test_copilot_writer_uses_shared_activity_schema(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    executor = CopilotExecutor("/tmp")
    executor._activity = ActivityWriter("copilot", store)
    executor._record("task-1", state="completed", text="done")

    assert store.get("task-1")["destination"] == "copilot"
