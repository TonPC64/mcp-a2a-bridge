import asyncio
import threading
import sqlite3
import time

from mcp_a2a_bridge.activity import ActivityLog, TEXT_PREVIEW_LIMIT
from mcp_a2a_bridge.activity_store import SQLiteActivityStore


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


def test_lock_is_not_bound_to_an_event_loop():
    """Regression guard for the ActivityLog cross-thread hang finding.

    The main stdio MCP event loop and the dashboard's uvicorn event loop run
    on different OS threads, both calling into the shared ActivityLog. An
    ``asyncio.Lock`` is bound to whichever event loop first awaits it, so a
    second thread/loop contending for it can hang -- it must never block the
    primary stdio bridge. Assert the internal lock is a plain
    ``threading.Lock`` (safe to acquire/release from any OS thread), not an
    ``asyncio.Lock``.
    """
    log = ActivityLog()
    assert isinstance(log._lock, type(threading.Lock()))
    assert not isinstance(log._lock, asyncio.Lock)


def test_record_blocks_across_threads_while_held_and_unblocks_promptly_on_release():
    """Demonstrates deterministic, safe contended access across OS threads.

    The main (test) thread directly holds ActivityLog's internal lock, exactly
    as a thread running the dashboard's uvicorn loop would while another
    thread's coroutine calls ``record()``. A second OS thread, running its own
    asyncio event loop, then calls ``record()``. Because the lock is genuinely
    held by the main thread, the worker thread is *guaranteed* (not merely
    likely) to still be blocked waiting for it -- this is a logical certainty
    from mutual exclusion, not a timing race, so asserting non-completion
    while the lock is held is deterministic rather than sleep-based luck.
    Once the lock is released, the worker must complete within a short,
    bounded timeout: if it hung (e.g. because the lock had reverted to an
    event-loop-bound ``asyncio.Lock``), the ``join(timeout=...)`` below would
    time out and the thread would still be alive, failing the assertion.
    """
    log = ActivityLog()
    worker_done = threading.Event()
    worker_error: list[BaseException] = []

    def worker() -> None:
        async def run() -> None:
            await log.record(
                task_id="from-worker-thread",
                agent="dashboard",
                kind="send_message",
                state="working",
                text="hello from another thread",
            )

        try:
            asyncio.run(run())
        except BaseException as exc:  # pragma: no cover - surfaced via assertion below
            worker_error.append(exc)
        finally:
            worker_done.set()

    log._lock.acquire()
    try:
        worker_thread = threading.Thread(target=worker)
        worker_thread.start()

        # The worker cannot possibly finish record() while we hold the lock:
        # this is guaranteed by mutual exclusion, not a timing assumption.
        still_blocked = worker_thread.join(timeout=0.3)
        assert still_blocked is None
        assert worker_thread.is_alive()
        assert not worker_done.is_set()
    finally:
        log._lock.release()

    worker_thread.join(timeout=5)
    assert not worker_thread.is_alive(), "record() hung after the lock was released"
    assert worker_done.is_set()
    assert not worker_error, f"worker thread raised: {worker_error}"

    entries = asyncio.run(log.list())
    assert [e.id for e in entries] == ["from-worker-thread"]

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


async def test_record_with_replaces_task_id_removes_placeholder_row_from_store(tmp_path):
    """Reconciliation must delete the OLD placeholder row from the shared store.

    The bridge records activity under a locally-generated placeholder id
    before it has the real A2A task_id, then re-records under the real id
    with replaces_task_id=<placeholder>. The SQLite write-through must not
    leave the placeholder row behind as a phantom duplicate.
    """
    store = SQLiteActivityStore(tmp_path / "activity.sqlite3")
    log = ActivityLog(store=store)

    await log.record(
        task_id="placeholder-1",
        agent="planner",
        kind="send_message",
        state="working",
        text="sending",
    )
    await log.record(
        task_id="real-task-1",
        agent="planner",
        kind="send_message",
        state="working",
        text="sending",
        replaces_task_id="placeholder-1",
    )

    assert store.get("placeholder-1") is None
    assert [e["id"] for e in store.list()] == ["real-task-1"]


class _UpsertFailingStore:
    """Stub store whose upsert() always raises, to simulate a contended/locked
    shared SQLite file (e.g. sqlite3.OperationalError: database is locked)."""

    def __init__(self) -> None:
        self.upsert_calls = 0
        self.delete_calls = 0

    def upsert(self, entry: dict) -> None:
        self.upsert_calls += 1
        raise sqlite3.OperationalError("database is locked")

    def delete(self, entry_id: str) -> None:
        self.delete_calls += 1
        raise sqlite3.OperationalError("database is locked")


class _DeleteFailingStore:
    """Stub store whose upsert() succeeds but delete() always raises, to
    exercise the replaces_task_id reconciliation path independently."""

    def __init__(self) -> None:
        self.upsert_calls = 0
        self.delete_calls = 0

    def upsert(self, entry: dict) -> None:
        self.upsert_calls += 1

    def delete(self, entry_id: str) -> None:
        self.delete_calls += 1
        raise sqlite3.OperationalError("database is locked")


class _TransientlyFailingStore:
    """Stub store that fails its first upsert() then succeeds, to simulate a
    momentary lock contention that clears on its own."""

    def __init__(self) -> None:
        self.upsert_calls = 0
        self.written: list[str] = []

    def upsert(self, entry: dict) -> None:
        self.upsert_calls += 1
        if self.upsert_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        self.written.append(entry["id"])

    def delete(self, entry_id: str) -> None:
        pass


class _SlowFailingStore:
    """Stub store whose upsert() blocks (as a contended SQLite write does under
    busy_timeout) before failing, to pin the cooldown's time anchor."""

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self.upsert_calls = 0

    def upsert(self, entry: dict) -> None:
        self.upsert_calls += 1
        time.sleep(self._delay)
        raise sqlite3.OperationalError("database is locked")

    def delete(self, entry_id: str) -> None:
        pass


async def test_cooldown_starts_when_the_write_failed_not_when_record_began():
    """A contended write can itself consume the whole busy_timeout. If the
    cooldown were anchored to the timestamp captured at the top of record(),
    the window would already be expired on return and the very next call would
    stall on the store again."""
    store = _SlowFailingStore(delay=0.3)
    log = ActivityLog(store=store, store_retry_cooldown=0.2)

    await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )
    assert store.upsert_calls == 1

    await log.record(
        task_id="t2", agent="planner", kind="send_message", state="working", text="hi again"
    )

    assert store.upsert_calls == 1  # still cooling down; not retried immediately


async def test_record_survives_store_upsert_failure_and_still_records_and_publishes():
    """A store.upsert() failure (e.g. contended SQLite file) must not propagate
    out of record(): the in-memory entry must still be stored and published."""
    store = _UpsertFailingStore()
    log = ActivityLog(store=store)
    subscriber = log.subscribe()

    entry = await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )

    assert entry.id == "t1"
    entries = await log.list()
    assert [e.id for e in entries] == ["t1"]
    published = subscriber.get(timeout=1)
    assert published["tasks"][0]["id"] == "t1"
    log.unsubscribe(subscriber)


async def test_record_survives_store_delete_failure_on_replaces_task_id_path():
    """A store.delete() failure during replaces_task_id reconciliation (the
    common path -- server.py passes replaces_task_id on every send_message made
    without an explicit task id) must not propagate out of record()."""
    store = _DeleteFailingStore()
    log = ActivityLog(store=store)

    await log.record(
        task_id="placeholder-1",
        agent="planner",
        kind="send_message",
        state="working",
        text="sending",
    )
    entry = await log.record(
        task_id="real-task-1",
        agent="planner",
        kind="send_message",
        state="working",
        text="sending",
        replaces_task_id="placeholder-1",
    )

    assert entry.id == "real-task-1"
    entries = await log.list()
    assert [e.id for e in entries] == ["real-task-1"]


async def test_record_pauses_store_writes_during_cooldown_after_a_failure():
    """After a store write failure, subsequent record() calls must skip the
    store until the cooldown expires, so sustained contention doesn't re-pay
    the busy_timeout on every A2A tool call."""
    store = _UpsertFailingStore()
    log = ActivityLog(store=store)

    await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )
    assert store.upsert_calls == 1

    await log.record(
        task_id="t2", agent="planner", kind="send_message", state="working", text="hi again"
    )

    assert store.upsert_calls == 1  # skipped while cooling down
    assert log._store_paused_until > time.time()


async def test_record_resumes_store_writes_after_the_cooldown_expires():
    """A momentary contention must not blind this process for its whole
    lifetime: once the cooldown expires, record() retries the store and a
    recovered store starts receiving writes again."""
    store = _TransientlyFailingStore()
    log = ActivityLog(store=store, store_retry_cooldown=0.0)

    await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )
    assert store.written == []

    await log.record(
        task_id="t2", agent="planner", kind="send_message", state="working", text="hi again"
    )

    assert store.upsert_calls == 2
    assert store.written == ["t2"]


async def test_record_logs_store_failure_warning_only_once_per_outage(capsys):
    """The stderr warning must be emitted once when the store starts failing,
    not on every subsequent record() call during the same outage."""
    store = _UpsertFailingStore()
    log = ActivityLog(store=store)

    await log.record(
        task_id="t1", agent="planner", kind="send_message", state="working", text="hi"
    )
    await log.record(
        task_id="t2", agent="planner", kind="send_message", state="working", text="hi again"
    )
    await log.record(
        task_id="t3", agent="planner", kind="send_message", state="working", text="hi again"
    )

    captured = capsys.readouterr()
    assert captured.err.count("shared activity store write failed") == 1


async def test_record_does_not_relog_on_every_retry_while_the_store_stays_down(capsys):
    """With a zero cooldown the store is retried on every call, but a single
    ongoing outage must still only warn once -- stderr is the bridge's only
    log channel and must not be flooded."""
    store = _UpsertFailingStore()
    log = ActivityLog(store=store, store_retry_cooldown=0.0)

    for index in range(5):
        await log.record(
            task_id=f"t{index}",
            agent="planner",
            kind="send_message",
            state="working",
            text="hi",
        )

    assert store.upsert_calls == 5  # retried every time, cooldown is zero
    captured = capsys.readouterr()
    assert captured.err.count("shared activity store write failed") == 1
