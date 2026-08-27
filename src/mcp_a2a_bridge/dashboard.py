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


def _login_page(error: bool = False) -> str:
    error_message = (
        '<p class="login-error" id="login-error" role="alert">Invalid dashboard token. Try again.</p>'
        if error
        else ""
    )
    described_by = ' aria-describedby="login-error"' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · A2A Bridge Dashboard</title><style>
:root {{ color:#edf6ff; background:#071226; font-family:Inter,ui-sans-serif,system-ui,sans-serif; font-synthesis:none; }}
* {{ box-sizing:border-box; }} body {{ display:grid; min-width:320px; min-height:100vh; place-items:center; margin:0; padding:1.25rem; background:radial-gradient(circle at 12% -5%,#37d7ff36,transparent 34rem),radial-gradient(circle at 90% 12%,#9b75ff30,transparent 30rem),linear-gradient(135deg,#061024,#10284a 52%,#091a34); }}
body::before {{ position:fixed; z-index:-1; inset:0; content:""; background-image:linear-gradient(#c7efff08 1px,transparent 1px),linear-gradient(90deg,#c7efff08 1px,transparent 1px); background-size:42px 42px; mask-image:linear-gradient(to bottom,black,transparent 76%); }}
.login-card {{ width:min(100%,28rem); padding:clamp(1.5rem,7vw,2.5rem); border:1px solid #d8f4ff36; border-radius:1.5rem; background:linear-gradient(125deg,#e9faff16,#a6dfff0a 48%,#a58dff13); box-shadow:inset 0 1px #ffffff38,inset 0 -1px #000b,0 1.8rem 4rem #0000005c,0 0 2.5rem #75c9ff0a; backdrop-filter:blur(28px) saturate(140%); }}
.eyebrow {{ margin:0 0 .65rem; color:#9bdcf5; font-size:.68rem; font-weight:800; letter-spacing:.15em; text-transform:uppercase; }} h1 {{ margin:0; font-size:clamp(2rem,9vw,3.2rem); line-height:.95; letter-spacing:-.06em; }} .lede {{ margin:1rem 0 1.75rem; color:#c2d2e5; line-height:1.55; }} form {{ display:grid; gap:.65rem; }} label {{ color:#dff7ff; font-size:.88rem; font-weight:750; }} input {{ width:100%; min-height:3rem; padding:.7rem .8rem; border:1px solid #b6eeec55; border-radius:.7rem; color:#edf6ff; background:#020c1f8c; font:inherit; }} input:focus-visible,button:focus-visible {{ outline:2px solid #a5eaff; outline-offset:3px; }} button {{ min-height:3rem; margin-top:.5rem; border:1px solid #b6eeec88; border-radius:.7rem; color:#071226; background:#a5eaff; box-shadow:inset 0 1px #fff8,0 .6rem 1.8rem #0006; font:inherit; font-weight:800; cursor:pointer; }} button:hover {{ background:#c3f2ff; }} .login-error {{ margin:.25rem 0 .5rem; padding:.7rem .8rem; border:1px solid #ff9da35c; border-radius:.65rem; color:#ffcbd0; background:#ff68751c; line-height:1.4; }} .hint {{ margin:1.2rem 0 0; color:#9bb5c9; font-size:.78rem; line-height:1.5; }}
@media (max-width:420px) {{ body {{ padding:.75rem; }} .login-card {{ border-radius:1.15rem; }} }} @media (prefers-reduced-motion:reduce) {{ * {{ transition-duration:.01ms!important; }} }}
</style></head><body><main class="login-card"><p class="eyebrow">Live operations</p><h1>A2A Bridge Dashboard</h1><p class="lede">Enter the dashboard token to view your connected agents and live activity.</p>{error_message}<form method="post"><label for="dashboard-token">Dashboard token</label><input id="dashboard-token" name="token" type="password" autocomplete="current-password" required autofocus{described_by}><button type="submit">Sign in</button></form><p class="hint">Your token is never stored in this form.</p></main></body></html>"""


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
        return _login_page()

    @app.post("/login")
    async def login(request: Request):
        token = parse_qs((await request.body()).decode()).get("token", [""])[0]
        if not bearer_token or not secrets.compare_digest(token, bearer_token):
            return HTMLResponse(_login_page(error=True), status_code=401)
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
