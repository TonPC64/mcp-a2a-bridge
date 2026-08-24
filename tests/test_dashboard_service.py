import asyncio

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.activity_store import SQLiteActivityStore
from mcp_a2a_bridge.dashboard_service import _run_poll_loop, build_poll_task


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


async def test_poll_task_survives_error_outside_store_list_and_retries():
    """Finding 2: an exception raised while building TaskActivity (i.e. AFTER
    store.list() returns successfully) must be caught and logged, not propagate --
    and the next poll tick must still be able to publish normally (real retry,
    not permanent suppression)."""
    store = FlakyStore()
    activity = ActivityLog()
    poll_once = build_poll_task(store, activity)

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