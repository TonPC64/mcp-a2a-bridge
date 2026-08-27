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
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

import uvicorn

from mcp_a2a_bridge.activity import ActivityLog, TaskActivity
from mcp_a2a_bridge.activity_store import SQLiteActivityStore, resolve_activity_db_path
from mcp_a2a_bridge.config import ConfigError, load_registry, resolve_config_path
from mcp_a2a_bridge.dashboard import build_dashboard_app
from mcp_a2a_bridge.hermes_audit import (
    list_hermes_audit_entries,
    merge_task_activity,
    resolve_hermes_audit_path,
)
from mcp_a2a_bridge.registry import AgentRegistry

DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 9100
DEFAULT_POLL_INTERVAL_S = 0.5

# How often the poll loop re-checks the stop condition while "sleeping" between
# ticks, so a SIGINT/SIGTERM-triggered shutdown is noticed promptly instead of
# being delayed by up to a full poll interval.
SHUTDOWN_CHECK_INTERVAL_S = 0.05


class _StopCondition(Protocol):
    """Anything exposing a ``should_exit`` flag, e.g. ``uvicorn.Server``."""

    should_exit: bool


def build_poll_task(
    store: SQLiteActivityStore,
    activity: ActivityLog,
    interval_s: float = DEFAULT_POLL_INTERVAL_S,
    hermes_audit_path: Path | None = None,
    agent_aliases: dict[str, str] | None = None,
) -> Callable[[], Awaitable[None]]:
    """Return a coroutine that does ONE poll-diff-publish cycle.

    ``interval_s`` is accepted so ``main()`` can drive this in a
    ``while True: await poll_once(); await asyncio.sleep(interval_s)`` loop; the
    tests call ``poll_once()`` directly without the sleep.
    """
    last_snapshot: dict | None = None
    hermes_audit_path = hermes_audit_path or resolve_hermes_audit_path()

    async def poll_once() -> None:
        nonlocal last_snapshot
        # The whole cycle -- store.list(), TaskActivity construction, and the
        # replace_all publish -- is one unit of work. Per the spec, any poll
        # failure (not just a store.list() failure) must be caught, logged,
        # and retried on the next tick. asyncio.CancelledError is a
        # BaseException (not Exception) in Python 3.8+, so it still
        # propagates for shutdown and is not swallowed here.
        try:
            rows = merge_task_activity(
                store.list(), list_hermes_audit_entries(hermes_audit_path, agent_aliases)
            )
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
                    source=row.get(
                        "source",
                        row["agent"]
                        if row["kind"] == "a2a_receive"
                        else "hermes" if row["kind"] == "a2a_call" else "mcp-a2a-bridge",
                    ),
                    destination=row.get(
                        "destination", "hermes" if row["kind"] == "a2a_receive" else row["agent"]
                    ),
                )
                for row in rows
            ]
            await activity.replace_all(entries)
        except Exception:  # noqa: BLE001 - must never crash the poll loop; retried next tick
            print(
                "mcp-a2a-bridge-dashboard: poll failed, will retry next tick:\n"
                + traceback.format_exc(),
                file=sys.stderr,
            )

    return poll_once


async def _run_poll_loop(
    poll_once: Callable[[], Awaitable[None]],
    interval_s: float,
    stop_condition: _StopCondition,
) -> None:
    """Run ``poll_once`` on a timer until ``stop_condition.should_exit`` is set.

    ``stop_condition`` is typically the ``uvicorn.Server`` instance driving the
    HTTP/SSE server: its signal handlers set ``should_exit`` on SIGINT/SIGTERM,
    so checking it here ties this loop's lifetime to the server's. The sleep
    between ticks is broken into short slices so shutdown is noticed within
    ``SHUTDOWN_CHECK_INTERVAL_S``, not delayed by a full ``interval_s``.
    """
    while not stop_condition.should_exit:
        await poll_once()
        remaining = interval_s
        while remaining > 0 and not stop_condition.should_exit:
            step = min(remaining, SHUTDOWN_CHECK_INTERVAL_S)
            await asyncio.sleep(step)
            remaining -= step


def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge-dashboard: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    host = os.environ.get("A2A_BRIDGE_DASHBOARD_HOST", DEFAULT_DASHBOARD_HOST)
    port = int(os.environ.get("A2A_BRIDGE_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT))
    bearer_token = os.environ.get("A2A_BRIDGE_DASHBOARD_TOKEN", "").strip() or None

    store = SQLiteActivityStore(resolve_activity_db_path())
    activity = ActivityLog()
    app = build_dashboard_app(registry, activity, bearer_token=bearer_token)

    async def _serve() -> None:
        aliases = {registry.entry(name).url: name for name in registry.names()}
        poll_once = build_poll_task(
            store,
            activity,
            hermes_audit_path=resolve_hermes_audit_path(),
            agent_aliases=aliases,
        )
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
        await asyncio.gather(
            _run_poll_loop(poll_once, DEFAULT_POLL_INTERVAL_S, server),
            server.serve(),
        )

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
