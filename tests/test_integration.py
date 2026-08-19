import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from mcp_a2a_bridge.config import AgentEntry, Registry
from mcp_a2a_bridge.registry import AgentRegistry
from mcp_a2a_bridge.server import build_server
from tests.echo_agent import build_app, build_card


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def payload(result):
    """Extract the structured dict from an MCP call_tool result."""
    if result.structured_content is not None:
        return result.structured_content
    # In mcp 2.0, results come as TextContent with JSON text
    if result.content and len(result.content) > 0:
        return json.loads(result.content[0].text)
    return result


@pytest.fixture(scope="module")
def echo_agent_url():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(build_card(port)),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/.well-known/agent-card.json", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        pytest.fail("echo agent did not start")

    yield url

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def bridge(echo_agent_url, tmp_path):
    registry = AgentRegistry(Registry(path=tmp_path / "agents.json", agents={}))
    return build_server(registry), registry, echo_agent_url


async def test_add_then_list_shows_real_skills(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    agent = payload(await server.call_tool("a2a_list_agents", {}))["agents"][0]

    assert agent["name"] == "echo"
    assert agent["reachable"] is True
    assert agent["streaming"] is True
    assert agent["skills"][0]["id"] == "echo"


async def test_send_message_round_trip(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    result = payload(
        await server.call_tool(
            "a2a_send_message", {"agent": "echo", "message": "hello world"}
        )
    )

    assert result["state"] == "completed"
    assert result["text"] == "echo: hello world"
    assert result["done"] is True


async def test_slow_agent_returns_task_id_then_can_be_polled(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    started = payload(
        await server.call_tool(
            "a2a_send_message", {"agent": "echo", "message": "slow", "timeout_s": 1}
        )
    )

    assert started["done"] is False
    assert started["state"] == "working"
    assert started["task_id"]

    polled = payload(
        await server.call_tool(
            "a2a_get_task", {"agent": "echo", "task_id": started["task_id"]}
        )
    )
    assert polled["state"] in {"working", "submitted"}


async def test_input_required_can_be_continued_with_same_task_id(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    first = payload(
        await server.call_tool("a2a_send_message", {"agent": "echo", "message": "ask"})
    )

    assert first["state"] == "input_required"
    assert first["text"] == "which city?"

    second = payload(
        await server.call_tool(
            "a2a_send_message",
            {"agent": "echo", "message": "Bangkok", "task_id": first["task_id"]},
        )
    )
    assert second["state"] == "completed"
    assert second["text"] == "echo: Bangkok"


async def test_unreachable_agent_does_not_break_other_agents(bridge):
    """Resolution (A): pre-configure dead agent with unused port, no private access."""
    server, registry, url = bridge

    # Add live agent
    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    
    # Pre-configure dead agent with an unused port
    dead_port = _free_port()
    dead_entry = AgentEntry(
        name="dead", url=f"http://127.0.0.1:{dead_port}", headers={}
    )
    registry._registry = Registry(
        path=registry._registry.path,
        agents={"echo": registry._registry.agents["echo"], "dead": dead_entry}
    )

    listed = payload(await server.call_tool("a2a_list_agents", {}))
    agents = {a["name"]: a for a in listed["agents"]}

    assert agents["echo"]["reachable"] is True
    assert agents["dead"]["reachable"] is False
    assert agents["dead"]["error"]
