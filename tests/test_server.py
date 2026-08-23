import json

import pytest
from a2a.types import AgentCard

from mcp_a2a_bridge import client as client_module
from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.client import A2AResult
from mcp_a2a_bridge.config import AgentEntry, Registry
from mcp_a2a_bridge.registry import AgentRegistry
from mcp_a2a_bridge.server import build_server

EXPECTED_TOOLS = {
    "a2a_list_agents",
    "a2a_send_message",
    "a2a_get_task",
    "a2a_cancel_task",
    "a2a_add_agent",
}


def payload(result):
    """Extract the structured dict from an MCP call_tool result."""
    if result.structured_content is not None:
        return result.structured_content
    # In mcp 2.0, results come as TextContent with JSON text
    if result.content and len(result.content) > 0:
        return json.loads(result.content[0].text)
    return result


def fake_registry(**agents):
    async def fetch(entry):
        return AgentCard(name=entry.name, description="d", version="1.0.0")

    return AgentRegistry(
        Registry(
            path=None,
            agents={n: AgentEntry(name=n, url=u, headers={}) for n, u in agents.items()},
        ),
        fetch_card=fetch,
    )


async def test_server_registers_exactly_five_tools():
    server = build_server(fake_registry())
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_every_tool_has_a_description():
    server = build_server(fake_registry())
    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"


async def test_list_agents_reports_reachability_instead_of_failing():
    async def fetch(entry):
        if entry.name == "bad":
            raise RuntimeError("refused")
        return AgentCard(name=entry.name, description="d", version="1.0.0")

    registry = AgentRegistry(
        Registry(
            path=None,
            agents={
                "good": AgentEntry(name="good", url="http://x", headers={}),
                "bad": AgentEntry(name="bad", url="http://y", headers={}),
            },
        ),
        fetch_card=fetch,
    )
    server = build_server(registry)

    agents = {a["name"]: a for a in payload(await server.call_tool("a2a_list_agents", {}))["agents"]}

    assert agents["good"]["reachable"] is True
    assert agents["bad"]["reachable"] is False
    assert "refused" in agents["bad"]["error"]


async def test_unknown_agent_is_a_tool_error():
    server = build_server(fake_registry(planner="http://x"))
    with pytest.raises(Exception) as exc:
        await server.call_tool("a2a_send_message", {"agent": "nope", "message": "hi"})
    assert "nope" in str(exc.value)


async def test_send_message_records_into_activity_log(monkeypatch):
    async def fake_send_message(entry, card, message, task_id=None, context_id=None, timeout_s=60):
        return A2AResult(state="completed", text="done", task_id="task-1", context_id="ctx-1", done=True)

    monkeypatch.setattr(client_module, "send_message", fake_send_message)

    activity = ActivityLog()
    server = build_server(fake_registry(planner="http://x"), activity=activity)

    await server.call_tool("a2a_send_message", {"agent": "planner", "message": "hi"})

    entries = await activity.list()
    assert len(entries) == 1
    assert entries[0].id == "task-1"
    assert entries[0].agent == "planner"
    assert entries[0].kind == "send_message"
    assert entries[0].state == "completed"
    assert entries[0].text == "done"


async def test_get_task_records_into_activity_log(monkeypatch):
    async def fake_get_task(entry, card, task_id):
        return A2AResult(state="working", text="still going", task_id=task_id, context_id=None, done=False)

    monkeypatch.setattr(client_module, "get_task", fake_get_task)

    activity = ActivityLog()
    server = build_server(fake_registry(planner="http://x"), activity=activity)

    await server.call_tool("a2a_get_task", {"agent": "planner", "task_id": "task-2"})

    entries = await activity.list()
    assert entries[0].id == "task-2"
    assert entries[0].kind == "get_task"
    assert entries[0].state == "working"


async def test_cancel_task_records_into_activity_log(monkeypatch):
    async def fake_cancel_task(entry, card, task_id):
        return A2AResult(state="canceled", text="", task_id=task_id, context_id=None, done=True)

    monkeypatch.setattr(client_module, "cancel_task", fake_cancel_task)

    activity = ActivityLog()
    server = build_server(fake_registry(planner="http://x"), activity=activity)

    await server.call_tool("a2a_cancel_task", {"agent": "planner", "task_id": "task-3"})

    entries = await activity.list()
    assert entries[0].id == "task-3"
    assert entries[0].kind == "cancel_task"
    assert entries[0].state == "canceled"
