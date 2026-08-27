import asyncio
import json

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.activity_store import SQLiteActivityStore
from mcp_a2a_bridge.dashboard_service import (
    DEFAULT_DASHBOARD_HOST,
    _run_poll_loop,
    build_poll_task,
)
from mcp_a2a_bridge.hermes_audit import (
    list_hermes_audit_entries,
    merge_task_activity,
    resolve_hermes_audit_path,
)


async def test_poll_task_publishes_new_snapshot_from_store(tmp_path):
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    activity = ActivityLog()
    poll_once = build_poll_task(store, activity, hermes_audit_path=tmp_path / "missing.jsonl")

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


def test_dashboard_binds_loopback_by_default():
    assert DEFAULT_DASHBOARD_HOST == "127.0.0.1"


async def test_poll_task_does_not_publish_when_snapshot_unchanged(tmp_path):
    store = SQLiteActivityStoreStub()
    activity = ActivityLog()
    poll_once = build_poll_task(store, activity, hermes_audit_path=tmp_path / "missing.jsonl")

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


class FlakyStore:
    """Stub store whose first list() call returns a row that fails downstream of
    store.list() itself -- e.g. missing keys that raise KeyError while building
    TaskActivity. Subsequent calls return a valid row, to prove retry works."""

    def __init__(self) -> None:
        self.calls = 0

    def list(self) -> list[dict]:
        self.calls += 1
        if self.calls == 1:
            return [{"id": "bad"}]  # missing agent/kind/state/... -> KeyError building TaskActivity
        return [
            {
                "id": "t1",
                "agent": "planner",
                "kind": "send_message",
                "state": "working",
                "text": "hi",
                "created_at": 1.0,
                "updated_at": 1.0,
            }
        ]


async def test_poll_task_survives_error_outside_store_list_and_retries(tmp_path):
    """Finding 2: an exception raised while building TaskActivity (i.e. AFTER
    store.list() returns successfully) must be caught and logged, not propagate --
    and the next poll tick must still be able to publish normally (real retry,
    not permanent suppression)."""
    store = FlakyStore()
    activity = ActivityLog()
    poll_once = build_poll_task(store, activity, hermes_audit_path=tmp_path / "missing.jsonl")

    await poll_once()  # bad row -> KeyError building TaskActivity; must not raise
    entries = await activity.list()
    assert entries == []  # nothing published on the failed attempt

    await poll_once()  # good row on the next tick -> must publish (proves retry)
    entries = await activity.list()
    assert len(entries) == 1
    assert entries[0].id == "t1"


class FakeServer:
    """Stand-in for uvicorn.Server exposing only the ``should_exit`` attribute
    that its real signal handlers set."""

    def __init__(self) -> None:
        self.should_exit = False


async def test_run_poll_loop_exits_when_should_exit_is_set():
    """Finding 1: the poll loop must terminate once the server's should_exit flag
    is set, instead of looping forever. asyncio.wait_for with a small timeout
    fails the test loudly if the loop hangs."""
    server = FakeServer()
    calls = 0

    async def poll_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            server.should_exit = True

    await asyncio.wait_for(_run_poll_loop(poll_once, 0.01, server), timeout=1)

    assert calls == 2


def test_resolve_hermes_audit_path_prefers_override_then_hermes_home(monkeypatch, tmp_path):
    override = tmp_path / "override.jsonl"
    home = tmp_path / "hermes-home"

    monkeypatch.setenv("A2A_BRIDGE_HERMES_AUDIT", str(override))
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert resolve_hermes_audit_path() == override

    monkeypatch.delenv("A2A_BRIDGE_HERMES_AUDIT")
    assert resolve_hermes_audit_path() == home / "a2a_audit.jsonl"

    monkeypatch.delenv("HERMES_HOME")
    assert resolve_hermes_audit_path().name == "a2a_audit.jsonl"
    assert resolve_hermes_audit_path().parent.name == ".hermes"


def test_hermes_audit_reader_skips_missing_malformed_and_partial_records(tmp_path):
    audit = tmp_path / "a2a_audit.jsonl"
    assert list_hermes_audit_entries(audit) == []

    audit.write_text(
        "not json\n"
        + json.dumps({"ts": 12.5, "direction": "outbound", "peer": "codex", "task_id": "t1", "summary": "Bearer secret-value"})
        + "\n"
        + '{"ts": 13, "direction": "outbound"'
    )

    assert list_hermes_audit_entries(audit) == [
        {
            "id": "hermes:t1",
            "agent": "codex",
            "kind": "a2a_call",
            "state": "recorded",
            "text": "Bearer [REDACTED]",
            "created_at": 12.5,
            "updated_at": 12.5,
        }
    ]


def test_hermes_audit_entries_merge_newest_first_and_cap_at_500(tmp_path):
    audit = tmp_path / "a2a_audit.jsonl"
    audit.write_text(
        "".join(
            json.dumps(
                {
                    "ts": index,
                    "direction": "outbound",
                    "peer": "codex",
                    "task_id": f"h{index}",
                    "summary": "x" * 600,
                }
            )
            + "\n"
            for index in range(501)
        )
    )
    bridge = [{"id": "bridge", "agent": "planner", "kind": "send_message", "state": "working", "text": "hi", "created_at": 1000.0, "updated_at": 1000.0}]

    merged = merge_task_activity(bridge, list_hermes_audit_entries(audit))

    assert len(merged) == 500
    assert merged[0]["id"] == "bridge"
    assert merged[1]["id"] == "hermes:h500"
    assert "hermes:h0" not in {entry["id"] for entry in merged}
    assert len(merged[1]["text"]) == 500


def test_merge_task_activity_dedupes_same_row_id_by_latest_update():
    merged = merge_task_activity(
        [{"id": "same", "agent": "codex", "kind": "a2a_receive", "state": "completed", "text": "done", "created_at": 1.0, "updated_at": 2.0}],
        [{"id": "same", "agent": "codex", "kind": "a2a_call", "state": "recorded", "text": "sent", "created_at": 1.0, "updated_at": 1.0}],
    )

    assert merged == [{"id": "same", "agent": "codex", "kind": "a2a_receive", "state": "completed", "text": "done", "created_at": 1.0, "updated_at": 2.0}]


def test_hermes_audit_maps_configured_peer_url_to_agent_name(tmp_path):
    audit = tmp_path / "a2a_audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "ts": 12.5,
                "direction": "outbound",
                "peer": "http://127.0.0.1:9011/",
                "task_id": "named",
                "summary": "hello",
            }
        )
        + "\n"
    )

    entries = list_hermes_audit_entries(
        audit, {"http://127.0.0.1:9011": "codex-co-developer"}
    )

    assert entries[0]["agent"] == "codex-co-developer"


def test_hermes_audit_includes_inbound_exchange_from_other_agent(tmp_path):
    audit = tmp_path / "a2a_audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "ts": 13.5,
                "direction": "inbound",
                "peer": "copilot",
                "task_id": "incoming",
                "summary": "Please inspect this task",
            }
        )
        + "\n"
    )

    entries = list_hermes_audit_entries(audit)

    assert entries[0]["agent"] == "copilot"
    assert entries[0]["kind"] == "a2a_receive"


def test_hermes_audit_keeps_inbound_and_outbound_with_same_task_id(tmp_path):
    audit = tmp_path / "a2a_audit.jsonl"
    rows = [
        {
            "ts": 10.0,
            "direction": "inbound",
            "peer": "copilot",
            "task_id": "shared",
            "summary": "incoming",
        },
        {
            "ts": 11.0,
            "direction": "outbound",
            "peer": "copilot",
            "task_id": "shared",
            "summary": "outgoing",
        },
    ]
    audit.write_text("".join(json.dumps(row) + "\n" for row in rows))

    entries = list_hermes_audit_entries(audit)

    assert {entry["id"] for entry in entries} == {
        "hermes:inbound:shared",
        "hermes:shared",
    }


async def test_poll_task_merges_real_temp_hermes_audit_into_api_output(tmp_path):
    audit = tmp_path / "a2a_audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "ts": 123.0,
                "direction": "outbound",
                "peer": "codex",
                "task_id": "task-test",
                "summary": "Inspect the dashboard",
            }
        )
        + "\n"
    )
    activity = ActivityLog()
    subscriber = activity.subscribe()
    await build_poll_task(SQLiteActivityStore(tmp_path / "activity.sqlite3"), activity, hermes_audit_path=audit)()

    entries = await activity.list()
    assert [entry.id for entry in entries] == ["hermes:task-test"]
    assert entries[0].state == "recorded"
    assert subscriber.get(timeout=1)["tasks"][0]["id"] == "hermes:task-test"
    activity.unsubscribe(subscriber)
