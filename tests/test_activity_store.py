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
    store.upsert(_entry("t1", 200.0, state="completed", created_at=999.0))

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


def test_delete_removes_the_row(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    store.upsert(_entry("t1", 100.0))

    store.delete("t1")

    assert store.get("t1") is None
    assert store.list() == []


def test_delete_of_nonexistent_id_is_a_harmless_noop(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    store.upsert(_entry("t1", 100.0))

    store.delete("does-not-exist")

    assert [e["id"] for e in store.list()] == ["t1"]
