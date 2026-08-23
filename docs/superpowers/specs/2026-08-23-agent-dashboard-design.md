# Agent Status & Task History Dashboard — Design

**Date:** 2026-08-23
**Status:** Approved for implementation planning

## Problem

`mcp-a2a-bridge` exposes remote A2A agents to coding-agent hosts over stdio
MCP tools (`a2a_list_agents`, `a2a_send_message`, `a2a_get_task`,
`a2a_cancel_task`, `a2a_add_agent`). There is no human-facing view of which
agents are configured/reachable or what tasks the bridge has recently sent,
polled, or canceled. A web dashboard should surface both, without changing
the MCP tool contract or A2A client semantics.

## Goals

- Show configured agents, their reachability, and their advertised skills
  (reuses existing `AgentRegistry` / `client.card_summary` logic).
- Show a rolling history of tasks the bridge has sent/polled/canceled,
  including their last known state (`working`, `input_required`,
  `completed`, `failed`, `canceled`, etc.) and a text preview.
- Zero impact on hosts that don't opt in: the dashboard is off by default
  and, if it fails to start, must never block or crash the stdio MCP server.
- No new remote calls to agents beyond what the MCP tools already make; the
  dashboard is a read-only observability layer over the bridge's own
  activity, not a new client of the agents.

## Non-goals

- Sending messages to agents from the dashboard UI (status + history only).
- Persisting history across bridge restarts (in-memory, bounded, is enough).
- Authentication — the dashboard binds to `127.0.0.1` only, matching the
  local, single-user nature of the bridge.

## Architecture

```
 stdio (MCP host) ──▶ MCPServer (server.py, unchanged transport)
                              │
                              ├─ AgentRegistry (existing)
                              └─ ActivityLog (new)
                                      ▲
                                      │ read-only
                              FastAPI dashboard app (new, dashboard.py)
                              ── GET /api/agents
                              ── GET /api/tasks
                              ── static files (dashboard/dist)
                                      ▲
                                      │ poll every 3s
                              React + Vite + TS SPA (dashboard/)
```

`main()` in `server.py`:

1. Builds the `AgentRegistry` and a new `ActivityLog` as today.
2. If `A2A_BRIDGE_DASHBOARD` is truthy, starts uvicorn serving the FastAPI
   app in a daemon background thread (its own asyncio event loop via
   `uvicorn.Server.run()` in the thread), bound to `127.0.0.1` and
   `A2A_BRIDGE_DASHBOARD_PORT` (default `9100`). Startup failures (e.g. port
   in use) are caught, logged to stderr, and do not prevent the stdio server
   from starting.
3. Runs the stdio MCP server in the main thread exactly as before.

The MCP tool functions in `build_server()` gain a few lines each to record
into `ActivityLog`; no tool signatures, return shapes, or existing tests'
expectations change.

## Data model: `ActivityLog`

New module `mcp_a2a_bridge/activity.py`, no A2A/network imports — mirrors the
separation already used by `config.py`.

```python
@dataclass
class TaskActivity:
    id: str            # task_id if present, else a generated uuid4 hex
    agent: str
    kind: str           # "send_message" | "get_task" | "cancel_task"
    state: str
    text: str            # truncated to 500 chars
    created_at: float    # time.time(), first time this id was seen
    updated_at: float    # time.time(), last update

class ActivityLog:
    def __init__(self, maxsize: int = 500): ...
    async def record(self, *, id: str | None, agent: str, kind: str,
                      state: str, text: str) -> None: ...
    async def list(self) -> list[TaskActivity]: ...  # newest first
```

Behavior:

- Keyed by `id` (the A2A `task_id`) so repeated `send_message` continuations
  and `get_task` polls update the same row's `state`/`text`/`updated_at`
  rather than creating duplicates.
- Calls with no `task_id` (e.g., a `send_message` that completes
  synchronously without ever surfacing one) get a generated id so they still
  show up as a single, self-contained history row.
- An `OrderedDict` keyed by `id`, `move_to_end` on update, `popitem(last=False)`
  eviction past `maxsize` — same bounded-LRU shape as `TTLTaskStore`, for
  consistency with existing code in this repo.
- Guarded by an `asyncio.Lock`, matching `TTLTaskStore`.

## API endpoints (`mcp_a2a_bridge/dashboard.py`)

- `GET /api/agents` — calls `registry.resolve_all(refresh=False)` and returns
  the same summary shape as the `a2a_list_agents` tool (name, configured_url,
  reachable, card_summary fields or error). No refresh param exposed yet;
  the dashboard just reflects whatever the bridge has already resolved.
- `GET /api/tasks` — returns `ActivityLog.list()` as JSON, newest first.
- `app.mount("/", StaticFiles(directory=dist_dir, html=True))` for the built
  SPA, added last so it doesn't shadow the `/api/*` routes.
- If `dashboard/dist` doesn't exist (frontend not built), the app still
  serves `/api/*`; the root path 404s with a clear message instead of
  crashing the process — keeps `dev` workflows (backend tests without a
  frontend build) working.

## Frontend (`dashboard/`)

Vite + React + TypeScript SPA, built with `npm run build` into
`dashboard/dist`, committed source but gitignored `dist/` and
`node_modules/` (extending `.gitignore`).

Components:

- `useApi(path, intervalMs)` — small hook: fetches once immediately, then
  polls every `intervalMs` (3000) via `setInterval`, cleans up on unmount,
  exposes `{ data, error, loading }`.
- `AgentList` — table/list of agents: name, reachable badge, url, skills.
- `TaskList` — table of activity rows: id (short), agent, kind, state badge,
  text preview, updated_at (relative time).
- `App` — layout wrapping both, using `useApi("/api/agents", 3000)` and
  `useApi("/api/tasks", 3000)`.

No routing library needed (single page, two sections). No state management
library — polling hook + local component state is enough for this scope.

## Testing

Backend (pytest, alongside existing `tests/`):

- `tests/test_activity.py` — recording new vs. updating existing ids,
  truncation of long text, eviction past `maxsize`, `list()` ordering
  (newest first).
- `tests/test_dashboard.py` — FastAPI `TestClient` hitting `/api/agents`
  (using the same `fake_registry()` helper pattern as `test_server.py`) and
  `/api/tasks` (seeded `ActivityLog`); asserts JSON shape and status codes.
- `server.py` changes covered by extending `tests/test_server.py` to assert
  tool calls populate the `ActivityLog` (e.g., after calling
  `a2a_send_message`, `activity.list()` has one entry with the right state).

Frontend (Vitest + React Testing Library, `dashboard/`):

- `AgentList.test.tsx` — renders reachable/unreachable agents correctly.
- `TaskList.test.tsx` — renders task rows, formats state badges.
- `useApi.test.ts` — mocks `fetch`, asserts polling calls it repeatedly and
  cleans up its interval on unmount.

## Rollout / config summary

| Env var | Default | Purpose |
|---|---|---|
| `A2A_BRIDGE_DASHBOARD` | unset (off) | Set to `1`/`true` to start the dashboard HTTP server |
| `A2A_BRIDGE_DASHBOARD_PORT` | `9100` | Port for the dashboard HTTP server |

README gets a new "Dashboard" section documenting these two variables, how
to build the frontend (`cd dashboard && npm install && npm run build`), and
that the dashboard is read-only (status + history, no message sending).