"""Legacy MCP 2025-11-25 server for clients that do not support MCP 2026."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_a2a_bridge import client
from mcp_a2a_bridge.config import ConfigError, load_registry, resolve_config_path
from mcp_a2a_bridge.registry import AgentRegistry, resolved_agent_summary

INSTRUCTIONS = (
    "Call remote A2A (Agent2Agent) agents. Start with a2a_list_agents to see "
    "which agents exist and what they can do. Use a2a_send_message to ask one "
    "to do something. If a call returns done=false with a task_id, poll "
    "a2a_get_task with that id."
)


def _text(value: Any) -> TextContent:
    return TextContent(type="text", text=json.dumps(value, ensure_ascii=False))


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    )


def build_server(registry: AgentRegistry) -> Server:
    server = Server("a2a-bridge", version="0.1.0", instructions=INSTRUCTIONS)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            _tool("a2a_list_agents", "List configured A2A agents with skills and reachability.", {
                "refresh": {"type": "boolean", "description": "Refetch cached agent cards."},
            }, []),
            _tool("a2a_send_message", "Send a message to an A2A agent and return its reply.", {
                "agent": {"type": "string"}, "message": {"type": "string"},
                "task_id": {"type": ["string", "null"]}, "context_id": {"type": ["string", "null"]},
                "timeout_s": {"type": "integer", "minimum": 1},
                "provider": {"type": ["string", "null"], "enum": ["litellm-auto", "github", None]},
            }, ["agent", "message"]),
            _tool("a2a_get_task", "Get the current state and output of a previously started A2A task.", {
                "agent": {"type": "string"}, "task_id": {"type": "string"},
            }, ["agent", "task_id"]),
            _tool("a2a_cancel_task", "Cancel a running A2A task and return its final state.", {
                "agent": {"type": "string"}, "task_id": {"type": "string"},
            }, ["agent", "task_id"]),
            _tool("a2a_add_agent", "Register an A2A agent by URL after fetching its agent card.", {
                "name": {"type": "string"}, "url": {"type": "string"},
                "headers": {"type": ["object", "null"], "additionalProperties": {"type": "string"}},
                "persist": {"type": "boolean"},
            }, ["name", "url"]),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "a2a_list_agents":
                agents = [resolved_agent_summary(item) for item in await registry.resolve_all(refresh=bool(arguments.get("refresh", False)))]
                return [_text({"agents": agents, "config_path": str(registry.config_path) if registry.config_path else None})]
            if name == "a2a_send_message":
                agent = str(arguments["agent"])
                provider = arguments.get("provider")
                if provider is not None and provider not in {"litellm-auto", "github"}:
                    raise ValueError("provider must be one of: litellm-auto, github")
                message = str(arguments["message"])
                if provider:
                    message = f"provider: {provider}\n{message}"
                result = await client.send_message(
                    registry.entry(agent), await registry.card(agent), message,
                    task_id=arguments.get("task_id"), context_id=arguments.get("context_id"),
                    timeout_s=int(arguments.get("timeout_s", 60)),
                )
                return [_text(result.to_dict())]
            if name == "a2a_get_task":
                result = await client.get_task(registry.entry(str(arguments["agent"])), await registry.card(str(arguments["agent"])), str(arguments["task_id"]))
                return [_text(result.to_dict())]
            if name == "a2a_cancel_task":
                result = await client.cancel_task(registry.entry(str(arguments["agent"])), await registry.card(str(arguments["agent"])), str(arguments["task_id"]))
                return [_text(result.to_dict())]
            if name == "a2a_add_agent":
                resolved = await registry.add(str(arguments["name"]), str(arguments["url"]), arguments.get("headers"), bool(arguments.get("persist", True)))
                summary = client.card_summary(resolved.card)
                summary.update(name=str(arguments["name"]), persisted=bool(arguments.get("persist", True)) and registry.config_path is not None)
                return [_text(summary)]
            raise ValueError(f"unknown tool: {name}")
        except Exception as exc:
            return [TextContent(type="text", text=str(exc))]

    return server


async def _run(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, InitializationOptions(
            server_name="a2a-bridge", server_version="0.1.0", capabilities=server.get_capabilities(
                notification_options=NotificationOptions(), experimental_capabilities={}
            ), instructions=INSTRUCTIONS,
        ))


if __name__ == "__main__":
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge legacy: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    asyncio.run(_run(build_server(registry)))
