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
