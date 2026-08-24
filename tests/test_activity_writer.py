from types import SimpleNamespace

from mcp_a2a_bridge.activity_store import SQLiteActivityStore
from mcp_a2a_bridge.activity_writer import ActivityWriter, build_activity_writer


def test_writer_records_a_bounded_inbound_task_lifecycle(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    writer = ActivityWriter("codex-co-developer", store=store)

    writer.record("task-1", source="copilot", state="working", text="x" * 600)
    writer.record("task-1", source="copilot", state="completed", text="done")

    assert store.get("task-1") == {
        "id": "task-1",
        "agent": "codex-co-developer",
        "source": "copilot",
        "destination": "codex-co-developer",
        "kind": "a2a_receive",
        "state": "completed",
        "text": "done",
        "created_at": store.get("task-1")["created_at"],
        "updated_at": store.get("task-1")["updated_at"],
    }


def test_writer_isolates_database_failures():
    class BrokenStore:
        def upsert(self, entry):
            raise OSError("disk unavailable")

    writer = ActivityWriter("claude-reviewer", store=BrokenStore())

    writer.record("task-1", source="remote", state="failed", text="failure")


def test_opt_in_writer_uses_safe_default_and_env_source(monkeypatch, tmp_path):
    monkeypatch.delenv("A2A_BRIDGE_DASHBOARD", raising=False)
    assert build_activity_writer("copilot") is None

    monkeypatch.setenv("A2A_BRIDGE_DASHBOARD", "1")
    monkeypatch.setenv("A2A_BRIDGE_ACTIVITY_DB", str(tmp_path / "activity.sqlite3"))
    monkeypatch.setenv("A2A_BRIDGE_ACTIVITY_SOURCE", "a" * 600)
    writer = build_activity_writer("copilot")
    assert writer is not None
    writer.record("task-1", source=writer.default_source, state="working", text="hi")

    row = SQLiteActivityStore(tmp_path / "activity.sqlite3").get("task-1")
    assert row["source"] == "a" * 256


def test_writer_uses_remote_for_an_unknown_context_peer(tmp_path):
    writer = ActivityWriter("copilot", store=SQLiteActivityStore(tmp_path / "activity.sqlite3"))

    assert writer.source_for(SimpleNamespace(call_context=SimpleNamespace())) == "remote"
