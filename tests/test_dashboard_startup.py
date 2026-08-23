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


def test_start_dashboard_returns_none_on_invalid_port(monkeypatch):
    monkeypatch.setenv("A2A_BRIDGE_DASHBOARD", "1")
    monkeypatch.setenv("A2A_BRIDGE_DASHBOARD_PORT", "not-a-number")

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