"""FastAPI app exposing agent status and task history for the dashboard UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
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
        agents = [
            resolved_agent_summary(item) for item in await registry.resolve_all(refresh=False)
        ]
        return {"agents": agents}

    @app.get("/api/tasks")
    async def get_tasks() -> dict:
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
