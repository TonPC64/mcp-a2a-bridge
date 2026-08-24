"""MCP server exposing remote A2A agents as tools."""

from __future__ import annotations

import os
import sys
import uuid

from mcp.server.mcpserver import MCPServer

from mcp_a2a_bridge import client
from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.activity_store import SQLiteActivityStore, resolve_activity_db_path
from mcp_a2a_bridge.config import ConfigError, load_registry, resolve_config_path
from mcp_a2a_bridge.registry import AgentRegistry, resolved_agent_summary

INSTRUCTIONS = (
    "Call remote A2A (Agent2Agent) agents. Start with a2a_list_agents to see "
    "which agents exist and what they can do. Use a2a_send_message to ask one "
    "to do something. If a call returns done=false with a task_id, the agent is "
    "still working: poll a2a_get_task with that id. If it returns "
    'state="input_required", reply by calling a2a_send_message again with the '
    "same task_id."
)


def _dashboard_enabled() -> bool:
    return os.environ.get("A2A_BRIDGE_DASHBOARD", "").strip().lower() in {"1", "true", "yes", "on"}


def build_activity_log() -> ActivityLog:
    """Build the bridge's ActivityLog, backed by the shared SQLite store when
    A2A_BRIDGE_DASHBOARD=1 so the standalone mcp-a2a-bridge-dashboard process
    (see dashboard_service.py) can see this process's activity. Unset (the
    default) keeps activity in-memory only -- no SQLite file is touched.
    """
    if not _dashboard_enabled():
        return ActivityLog()
    try:
        return ActivityLog(store=SQLiteActivityStore(resolve_activity_db_path()))
    except Exception as exc:
        print(
            f"mcp-a2a-bridge: dashboard activity persistence disabled -- "
            f"could not open activity store: {exc}",
            file=sys.stderr,
        )
        return ActivityLog()


def build_server(registry: AgentRegistry, activity: ActivityLog | None = None) -> MCPServer:
    server = MCPServer(name="a2a-bridge", instructions=INSTRUCTIONS)
    activity = activity if activity is not None else ActivityLog()

    @server.tool()
    async def a2a_list_agents(refresh: bool = False) -> dict:
        """List configured A2A agents with their skills and reachability.

        Set refresh=true to re-fetch agent cards that were previously cached.
        """
        agents = [
            resolved_agent_summary(item) for item in await registry.resolve_all(refresh=refresh)
        ]
        return {
            "agents": agents,
            "config_path": str(registry.config_path) if registry.config_path else None,
        }

    @server.tool()
    async def a2a_send_message(
        agent: str,
        message: str,
        task_id: str | None = None,
        context_id: str | None = None,
        timeout_s: int = 60,
    ) -> dict:
        """Send a message to an A2A agent and return its reply.

        Pass task_id to continue an existing task, for example to answer an
        agent that returned state="input_required". If the agent is still
        working when timeout_s elapses, this returns done=false with a task_id
        to poll rather than blocking.
        """
        local_activity_task_id = task_id or uuid.uuid4().hex
        activity_task_id = local_activity_task_id
        await activity.record(
            task_id=activity_task_id,
            agent=agent,
            kind="send_message",
            state="working",
            text="Dispatched to agent.",
        )

        async def record_update(result: client.A2AResult) -> None:
            nonlocal activity_task_id
            activity_task_id = result.task_id or activity_task_id
            await activity.record(
                task_id=activity_task_id,
                agent=agent,
                kind="send_message",
                state="working" if result.state == "submitted" else result.state,
                text=result.text,
                replaces_task_id=(
                    local_activity_task_id
                    if task_id is None and activity_task_id != local_activity_task_id
                    else None
                ),
            )

        try:
            entry = registry.entry(agent)
            card = await registry.card(agent)
            result = await client.send_message(
                entry,
                card,
                message,
                task_id=task_id,
                context_id=context_id,
                timeout_s=timeout_s,
                on_update=record_update,
            )
        except Exception as exc:
            await activity.record(
                task_id=activity_task_id,
                agent=agent,
                kind="send_message",
                state="failed",
                text=str(exc),
            )
            raise
        activity_task_id = result.task_id or activity_task_id
        await activity.record(
            task_id=activity_task_id,
            agent=agent,
            kind="send_message",
            state=result.state,
            text=result.text,
            replaces_task_id=(
                local_activity_task_id
                if task_id is None and activity_task_id != local_activity_task_id
                else None
            ),
        )
        return result.to_dict()

    @server.tool()
    async def a2a_get_task(agent: str, task_id: str) -> dict:
        """Get the current state and output of a previously started A2A task."""
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.get_task(entry, card, task_id)
        await activity.record(
            task_id=result.task_id or task_id,
            agent=agent,
            kind="get_task",
            state=result.state,
            text=result.text,
        )
        return result.to_dict()

    @server.tool()
    async def a2a_cancel_task(agent: str, task_id: str) -> dict:
        """Cancel a running A2A task and return its final state."""
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.cancel_task(entry, card, task_id)
        await activity.record(
            task_id=result.task_id or task_id,
            agent=agent,
            kind="cancel_task",
            state=result.state,
            text=result.text,
        )
        return result.to_dict()

    @server.tool()
    async def a2a_add_agent(
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
        persist: bool = True,
    ) -> dict:
        """Register a new A2A agent by its base URL.

        The agent card is fetched first, so an unreachable URL is rejected
        instead of being saved. With persist=true the agent is written to the
        shared registry file and stays available in future sessions.
        """
        resolved = await registry.add(name, url, headers, persist)
        summary = client.card_summary(resolved.card)
        summary["name"] = name
        summary["persisted"] = persist and registry.config_path is not None
        return summary

    return server


def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    activity = build_activity_log()
    build_server(registry, activity).run(transport="stdio")


if __name__ == "__main__":
    main()
