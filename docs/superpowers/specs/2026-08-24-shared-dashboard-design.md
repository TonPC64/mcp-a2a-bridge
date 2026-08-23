# Shared, Multi-Process Dashboard Design

## Problem

The dashboard (added in `2026-08-23-agent-dashboard-design.md`, then upgraded to
SSE) is embedded inside each `mcp-a2a-bridge` process and keeps its task
activity in an in-memory `OrderedDict`. This has two consequences confirmed by
live testing on 2026-08-24:

1. **Cross-process activity is invisible.** Every MCP host (Copilot CLI,
   Hermes, etc.) spawns its own `mcp-a2a-bridge` process, each with its own
   dashboard and its own empty activity log. A real A2A call handled by
   process A never appears in process B's dashboard, even if B is the one the
   user has open in a browser — the browser only sees new activity after a
   *page reload* if it happens to hit A's process again (it usually doesn't,
   since each process binds its own port instance and the browser holds an
   open SSE connection to whichever one answered first).
2. **No multi-device viewing.** The dashboard binds `127.0.0.1` only, so a
   second device on the same network (e.g. a phone or another laptop) cannot
   open it at all.

## Goals

- One dashboard reachable from any device on the local network, showing
  activity from every `mcp-a2a-bridge` process on the machine, live.
- Preserve today's read-only guarantee: the dashboard never sends messages to
  agents.
- A crashed or slow dashboard must never block or crash a bridge's stdio MCP
  loop. A crashed or restarted bridge must never take the dashboard down.

## Non-goals

- Authentication/authorization. Per explicit decision, the dashboard is
  unauthenticated and trusts the local network, same as other local dev
  tools (e.g. a Vite dev server). No token, no login.
- Cross-machine access (VPN/tunnel/relay). Only same-LAN devices.
- Persisting activity forever. The existing TTL/LRU bound (500 entries) is
  kept, now enforced in SQLite instead of an `OrderedDict`.

## Architecture

```
mcp-a2a-bridge (Copilot's)     mcp-a2a-bridge (Hermes')     mcp-a2a-bridge (other host)
  ActivityLog ---+               ActivityLog ---+             ActivityLog ---+
                 |                               |                            |
                 +-------------------------------+----------------------------+
                                 |
                                 v
                 ~/.config/a2a-bridge/activity.sqlite3
                                 |
                                 v  (poll every 500ms, diff, broadcast)
                  mcp-a2a-bridge-dashboard (standalone long-lived process)
                  binds 0.0.0.0:9100
                                 |
                    SSE to all connected browsers
              (this Mac, phone, other laptop, ...)
```

### Components

1. **`SQLiteActivityStore`** (new, replaces the `OrderedDict` inside
   `ActivityLog`) — same shape as the existing `SQLiteTaskStore` pattern in
   this repo (`src/mcp_a2a_bridge/sqlite_task_store.py`): a single SQLite
   file at a well-known path (`~/.config/a2a-bridge/activity.sqlite3`,
   override via `A2A_BRIDGE_ACTIVITY_DB`), WAL mode enabled so concurrent
   readers (dashboard) and writers (N bridges) don't block each other.
   `ActivityLog.record()` / `.list()` keep their current async signatures;
   only the storage backend changes. Every bridge process, regardless of
   host, writes into the same file.
2. **`mcp-a2a-bridge-dashboard`** (new console script / entry point) — a
   standalone process, no stdio MCP loop, no dependency on being spawned by
   an MCP host. Responsibilities:
   - Serve the existing FastAPI app (`/`, `/api/agents`, `/api/tasks`,
     `/api/agents/events`, `/api/tasks/events`) unchanged in shape.
   - Own a background task that polls `SQLiteActivityStore` on an interval
     (500ms), and only publishes an SSE `tasks` event when the snapshot
     actually differs from the last one sent (dict equality check) — this is
     what makes updates "live" across processes without needing every bridge
     to also speak HTTP to the dashboard.
   - Resolve agents itself from `agents.json` (unchanged — the dashboard
     already does this independent of any bridge process).
   - Bind `0.0.0.0` by default (configurable via
     `A2A_BRIDGE_DASHBOARD_HOST`, default `0.0.0.0`), port via
     `A2A_BRIDGE_DASHBOARD_PORT` (default `9100`, unchanged).
3. **Bridge processes** — `A2A_BRIDGE_DASHBOARD=1` changes meaning: instead
   of "spawn an embedded dashboard HTTP server on this process," it now
   means "back my `ActivityLog` with the shared SQLite store instead of an
   in-memory dict." The embedded FastAPI server and `start_dashboard()` /
   `DashboardHandle` in `server.py` are removed — there is no per-process
   dashboard anymore. If `A2A_BRIDGE_DASHBOARD` is unset, `ActivityLog`
   stays in-memory only (today's default, no behavior change, no SQLite
   file touched) — this preserves "opt-in, zero-cost when disabled."

## Data flow

1. User (via any MCP host) sends an A2A request → tool handler in
   `server.py` calls `activity.record(...)` at each state transition
   (working, heartbeats via `a2a_get_task` polling, terminal), exactly as
   today.
2. `ActivityLog.record()` writes the entry to the shared SQLite file
   (`INSERT ... ON CONFLICT DO UPDATE`, same upsert-by-task-id shape as
   `SQLiteTaskStore`) instead of an in-memory dict, still evicting past the
   500-entry bound.
3. The dashboard process's poll loop reads the full task list from SQLite,
   compares it to the last broadcast snapshot, and if different, calls the
   existing `SnapshotSubscribers.publish()` — reusing today's SSE fan-out
   mechanism unchanged.
4. Every connected browser (any device on the LAN) receives the update
   within one poll interval (up to 500ms), regardless of which bridge
   process handled the underlying A2A call.

## Error handling

- SQLite write failures inside a bridge's `activity.record()` are caught and
  logged to stderr; the tool call itself still returns its result to the
  MCP host. Dashboard visibility degrading must never fail a real A2A call.
- SQLite read/poll failures inside the dashboard process are caught, logged,
  and retried on the next poll tick; transient file-lock contention does not
  crash the SSE stream.
- If the shared SQLite file doesn't exist yet (no bridge has run with the
  flag on), the dashboard creates the schema itself on startup (same
  `CREATE TABLE IF NOT EXISTS` pattern as `SQLiteTaskStore`) so it can be
  started before or after any bridge.

## Testing

- `SQLiteActivityStore`: round-trip record/list, upsert-by-id, TTL/LRU
  eviction bound, matching the existing `test_activity.py` behavioral
  contract.
- **Cross-process regression test** (directly reproduces the bug found
  2026-08-24): two separate `ActivityLog` instances pointed at the same
  SQLite file (simulating two bridge processes); a `record()` on instance A
  must be visible via `list()` on instance B, and via the dashboard's SSE
  stream when the dashboard polls between the two.
- Dashboard service tests: replace the old embedded-`start_dashboard()`
  tests (removed) with tests that build the standalone app against a real
  SQLite file and assert the poll-diff-publish loop only emits on actual
  changes (not every poll tick).
- Existing `test_dashboard.py` SSE contract tests (initial snapshot + update
  events) are kept, adapted to the new poll-driven publish path.

## Migration / compatibility notes

- README updated: dashboard is now started via `mcp-a2a-bridge-dashboard`,
  run once, independent of any bridge; bridges just need
  `A2A_BRIDGE_DASHBOARD=1` to persist into the shared store.
- Existing single-process embedded-dashboard code path is deleted, not kept
  as a fallback (explicit decision) — simpler to maintain one path.
- No auth token (explicit decision) — document this plainly as a
  local-network-trust tool, not for use on untrusted networks.
