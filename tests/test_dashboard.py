import asyncio
import contextlib
import json
import threading
import time

from a2a.types import AgentCard
from fastapi.testclient import TestClient
from starlette.requests import Request

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


async def event_stream(app, path: str):
    route = next(route for route in app.routes if route.path == path)
    request = Request({"type": "http", "method": "GET", "path": path, "headers": []})
    return await route.endpoint(request)


async def next_event(response):
    chunk = await response.body_iterator.__anext__()
    lines = dict(line.split(": ", 1) for line in chunk.decode().strip().splitlines())
    return lines["event"], json.loads(lines["data"])


async def test_task_stream_sends_initial_snapshot_and_record_update():
    registry = fake_registry()
    activity = ActivityLog()
    app = build_dashboard_app(registry, activity)
    response = await event_stream(app, "/api/tasks/events")

    assert await next_event(response) == ("tasks", {"tasks": []})

    await activity.record(
        task_id="t1", agent="planner", kind="send_message", state="completed", text="done"
    )
    event, data = await next_event(response)
    assert event == "tasks"
    assert data["tasks"][0]["id"] == "t1"

    await response.body_iterator.aclose()


async def test_task_stream_sends_working_heartbeat_and_terminal_snapshots():
    """Each ActivityLog update must reach SSE clients without waiting for a poll."""
    activity = ActivityLog()
    app = build_dashboard_app(fake_registry(), activity)
    response = await event_stream(app, "/api/tasks/events")
    assert await next_event(response) == ("tasks", {"tasks": []})

    for state, text in [("working", "started"), ("working", "still working"), ("completed", "done")]:
        await activity.record(
            task_id="t1", agent="planner", kind="send_message", state=state, text=text
        )
        event, data = await next_event(response)
        assert event == "tasks"
        assert data["tasks"][0]["state"] == state
        assert data["tasks"][0]["text"] == text

    await response.body_iterator.aclose()


async def test_agent_stream_sends_initial_snapshot_and_add_update():
    registry = fake_registry()
    app = build_dashboard_app(registry, ActivityLog())
    response = await event_stream(app, "/api/agents/events")

    assert await next_event(response) == ("agents", {"agents": []})

    await registry.add("planner", "http://x", None, persist=False)
    event, data = await next_event(response)
    assert event == "agents"
    assert data["agents"] == [
        {
            "name": "planner",
            "configured_url": "http://x",
            "reachable": True,
            "description": "d",
            "version": "1.0.0",
            "url": None,
            "streaming": False,
            "input_modes": [],
            "output_modes": [],
            "skills": [],
        }
    ]

    await response.body_iterator.aclose()
    assert registry.subscriber_count == 0


async def test_closing_task_stream_unsubscribes_it():
    activity = ActivityLog()
    app = build_dashboard_app(fake_registry(), activity)
    response = await event_stream(app, "/api/tasks/events")
    await next_event(response)
    await response.body_iterator.aclose()

    await activity.record(
        task_id="t1", agent="planner", kind="send_message", state="completed", text="done"
    )
    assert activity.subscriber_count == 0


def test_activity_publish_from_another_thread_notifies_subscriber():
    activity = ActivityLog()
    subscriber = activity.subscribe()

    def record() -> None:
        asyncio.run(
            activity.record(
                task_id="t1", agent="planner", kind="send_message", state="completed", text="done"
            )
        )

    thread = threading.Thread(target=record)
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert subscriber.get(timeout=1)["tasks"][0]["id"] == "t1"
    activity.unsubscribe(subscriber)


async def test_task_stream_cancellation_does_not_leave_blocked_worker_thread():
    """The SSE generator's wait must be interruptible so the process can shut down.

    Regression test for: cancelling the SSE stream task used to leave an
    ``asyncio_*`` worker thread permanently blocked inside
    ``subscriber.get()`` (no timeout), which made
    ``loop.shutdown_default_executor()`` hang forever and prevented the
    dashboard process from exiting on SIGINT.
    """
    activity = ActivityLog()
    app = build_dashboard_app(fake_registry(), activity)
    response = await event_stream(app, "/api/tasks/events")

    # Consume the initial snapshot so the generator is parked on the
    # blocking wait for the next update (no publish is pending).
    assert await next_event(response) == ("tasks", {"tasks": []})

    before_workers = {th.ident for th in threading.enumerate()}

    task = asyncio.ensure_future(response.body_iterator.__anext__())
    await asyncio.sleep(0.05)
    task.cancel()

    # A prompt cancellation must not hang. With the old unbounded
    # `subscriber.get()`, this wait_for would time out because cancelling
    # the asyncio task does not unblock the underlying OS thread.
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(task, timeout=2)

    await response.body_iterator.aclose()
    activity.unsubscribe(activity.subscribe())  # no-op safety, keeps counts sane

    # The real property under test: no lingering worker thread stuck on the
    # queue, and the executor can be shut down promptly.
    await asyncio.wait_for(asyncio.get_running_loop().shutdown_default_executor(), timeout=2)

    after_workers = {th.ident for th in threading.enumerate()}
    leaked = after_workers - before_workers
    assert not leaked, f"worker thread(s) left running after cancellation: {leaked}"


async def test_task_stream_push_latency_stays_fast():
    """Publishing a snapshot must reach a connected client almost immediately.

    Guards against a fix that turns the interruptible wait into a slow poll.
    """
    activity = ActivityLog()
    app = build_dashboard_app(fake_registry(), activity)
    response = await event_stream(app, "/api/tasks/events")
    assert await next_event(response) == ("tasks", {"tasks": []})

    start = time.monotonic()
    await activity.record(
        task_id="t1", agent="planner", kind="send_message", state="completed", text="done"
    )
    event, data = await asyncio.wait_for(next_event(response), timeout=1)
    elapsed = time.monotonic() - start

    assert event == "tasks"
    assert data["tasks"][0]["id"] == "t1"
    assert elapsed < 1.0

    await response.body_iterator.aclose()
