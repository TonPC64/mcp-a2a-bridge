"""Standalone dashboard process: one long-lived service showing live task
activity from every mcp-a2a-bridge process on the machine.

Run via the ``mcp-a2a-bridge-dashboard`` console script. Independent of any
bridge process -- it can be started before or after any bridge, and stays up
across bridge restarts.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

import uvicorn

from mcp_a2a_bridge.activity import ActivityLog, TaskActivity
from mcp_a2a_bridge.activity_store import SQLiteActivityStore, resolve_activity_db_path
from mcp_a2a_bridge.config import ConfigError, load_registry, resolve_config_path
from mcp_a2a_bridge.dashboard import build_dashboard_app
from mcp_a2a_bridge.registry import AgentRegistry

DEFAULT_DASHBOARD_HOST = "0.0.0.0"
DEFAULT_DASHBOARD_PORT = 9100
DEFAULT_POLL_INTERVAL_S = 0.5


def build_poll_task(
    store: SQLiteActivityStore,
    activity: ActivityLog,
    interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> Callable[[], Awaitable[None]]:
    """Return a coroutine that does ONE poll-diff-publish cycle.

    ``interval_s`` is accepted so ``main()`` can drive this in a
    ``while True: await poll_once(); await asyncio.sleep(interval_s)`` loop; the
    tests call ``poll_once()`` directly without the sleep.
    """
    last_snapshot: dict | None = None

    async def poll_once() -> None:
        nonlocal last_snapshot
        try:
            rows = store.list()
        except Exception as exc:  # SQLite read failures must never crash the poll loop
            print(f"mcp-a2a-bridge-dashboard: poll failed: {exc}", file=sys.stderr)
            return

        snapshot = {"tasks": rows}
        if snapshot == last_snapshot:
            return
        last_snapshot = snapshot

        entries = [
            TaskActivity(
                id=row["id"],
                agent=row["agent"],
                kind=row["kind"],
                state=row["state"],
                text=row["text"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        await activity.replace_all(entries)

    return poll_once


async def _run_poll_loop(poll_once: Callable[[], Awaitable[None]], interval_s: float) -> None:
    while True:
        await poll_once()
        await asyncio.sleep(interval_s)


def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge-dashboard: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    store = SQLiteActivityStore(resolve_activity_db_path())
    activity = ActivityLog()
    app = build_dashboard_app(registry, activity)

    host = os.environ.get("A2A_BRIDGE_DASHBOARD_HOST", DEFAULT_DASHBOARD_HOST)
    port = int(os.environ.get("A2A_BRIDGE_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT))

    async def _serve() -> None:
        poll_once = build_poll_task(store, activity)
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
        await asyncio.gather(
            _run_poll_loop(poll_once, DEFAULT_POLL_INTERVAL_S),
            server.serve(),
        )

    asyncio.run(_serve())


if __name__ == "__main__":
    main()