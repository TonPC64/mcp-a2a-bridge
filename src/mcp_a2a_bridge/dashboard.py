"""FastAPI app exposing agent status and task history for the dashboard UI."""

from __future__ import annotations

import asyncio
import json
import queue
import secrets
from urllib.parse import parse_qs
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from mcp_a2a_bridge.activity import ActivityLog
from mcp_a2a_bridge.registry import AgentRegistry, resolved_agent_summary

_PACKAGE_DIST_DIR = Path(__file__).resolve().parent / "dashboard_dist"
_SOURCE_DIST_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"
DIST_DIR = _PACKAGE_DIST_DIR if _PACKAGE_DIST_DIR.is_dir() else _SOURCE_DIST_DIR

# How long each blocking `Queue.get()` slice waits before giving control back
# to the event loop. A published snapshot is delivered as soon as it arrives
# (queue.get returns immediately once an item is available), so this value
# only bounds worst-case *shutdown/cancellation* latency, not push latency.
# 200ms is short enough that Ctrl+C/SIGINT shutdown feels instant while still
# avoiding a busy loop.
_QUEUE_POLL_INTERVAL_SECONDS = 0.2


async def _wait_for_next(subscriber: "queue.Queue[dict]") -> dict:
    """Block for the next published snapshot without wedging a worker thread.

    ``subscriber.get()`` with no timeout blocks its OS worker thread
    indefinitely; cancelling the awaiting asyncio task does not unblock that
    thread, which used to hang process shutdown forever inside
    ``loop.shutdown_default_executor()``. Waiting in short timed slices lets
    each worker-thread call return on its own, so cancellation is observed
    between slices and the executor can be joined promptly.
    """
    while True:
        try:
            return await asyncio.to_thread(subscriber.get, True, _QUEUE_POLL_INTERVAL_SECONDS)
        except queue.Empty:
            continue


def build_dashboard_app(
    registry: AgentRegistry,
    activity: ActivityLog,
    dist_dir: Path | None = None,
    bearer_token: str | None = None,
) -> FastAPI:
    dist_dir = dist_dir if dist_dir is not None else DIST_DIR
    app = FastAPI(title="a2a-bridge dashboard")

    def authenticated(request: Request) -> bool:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        supplied_token = token if scheme.lower() == "bearer" else request.cookies.get("dashboard_token", "")
        return bool(supplied_token) and secrets.compare_digest(supplied_token, bearer_token or "")

    @app.middleware("http")
    async def require_dashboard_token(request: Request, call_next):
        if not bearer_token or request.url.path == "/login" or authenticated(request):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Dashboard authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form() -> str:
        return """<!doctype html><title>Dashboard login</title><form method="post"><label>Token <input name="token" type="password" required autofocus></label><button>Login</button></form>"""

    @app.post("/login")
    async def login(request: Request):
        token = parse_qs((await request.body()).decode()).get("token", [""])[0]
        if not bearer_token or not secrets.compare_digest(token, bearer_token):
            return JSONResponse({"detail": "Invalid dashboard token"}, status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            "dashboard_token",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

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
                    **({"source": e.source} if e.source else {}),
                    **({"destination": e.destination} if e.destination else {}),
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
                    yield encode_sse(event, await _wait_for_next(subscriber))
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
