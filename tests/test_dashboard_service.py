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