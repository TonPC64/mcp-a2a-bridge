"""FastAPI app exposing agent status and task history for the dashboard UI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.registry import AgentRegistry, resolved_agent_summary

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"


def build_dashboard_app(
    registry: AgentRegistry,
    activity: ActivityLog,
    dist_dir: Path | None = None,
) -> FastAPI:
    dist_dir = dist_dir if dist_dir is not None else DIST_DIR
    app = FastAPI(title="a2a-bridge dashboard")

    @app.get("/api/agents")
    async def get_agents() -> dict:
        return await agent_snapshot()

    @app.get("/api/tasks")
    async def get_tasks() -> dict:
        return await task_snapshot()

    async def agent_snapshot() -> dict:
        return {
            "agents": [
                resolved_agent_summary(item) for item in await registry.resolve_all(refresh=False)
            ]
        }

    async def task_snapshot() -> dict:
        entries = await activity.list()
        return {
            "tasks": [
                {
                    "id": e.id,
                    "agent": e.agent,
                    "kind": e.kind,
                    "state": e.state,
                    "text": e.text,
                    "created_at": e.created_at,
                    "updated_at": e.updated_at,
                }
                for e in entries
            ]
        }

    def stream(event: str, subscribe, unsubscribe, initial_snapshot):
        async def events():
            subscriber = subscribe()
            try:
                yield encode_sse(event, await initial_snapshot())
                while True:
                    yield encode_sse(event, await asyncio.to_thread(subscriber.get))
            finally:
                unsubscribe(subscriber)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/agents/events")
    async def stream_agents(request: Request) -> StreamingResponse:
        return stream("agents", registry.subscribe, registry.unsubscribe, agent_snapshot)

    @app.get("/api/tasks/events")
    async def stream_tasks(request: Request) -> StreamingResponse:
        return stream("tasks", activity.subscribe, activity.unsubscribe, task_snapshot)

    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="dashboard")
    else:

        @app.get("/")
        async def missing_build() -> JSONResponse:
            return JSONResponse(
                {"error": f"Dashboard frontend not built. Expected files in {dist_dir}."},
                status_code=404,
            )

    return app


def encode_sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
