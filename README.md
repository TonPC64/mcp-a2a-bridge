# mcp-a2a-bridge

An MCP server that lets coding agents — GitHub Copilot CLI, Claude Code, Codex,
and Hermes — call remote [A2A](https://a2a-protocol.org) agents.

The bridge is an A2A client and the active home for the bundled Copilot A2A
server.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Install

```bash
cd ~/WorkSpace/mcp-a2a-bridge
uv venv --python 3.11
uv pip install -e ".[dev]"
```

## Configure agents

The registry is shared by every host. Resolution order:

1. `$A2A_BRIDGE_CONFIG`
2. `./.a2a-agents.json` in the current directory
3. `~/.config/a2a-bridge/agents.json`

```json
{
  "agents": {
    "planner": {
      "url": "http://localhost:9001",
      "headers": {}
    }
  }
}
```

`url` is the agent's base URL; the bridge appends
`/.well-known/agent-card.json`. You can also add agents at runtime with the
`a2a_add_agent` tool, which writes back to this file.

## Register with your host

All four hosts run the same command.

**GitHub Copilot CLI** — add to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "a2a": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "/Users/YOU/WorkSpace/mcp-a2a-bridge", "mcp-a2a-bridge"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add a2a -- uvx --from ~/WorkSpace/mcp-a2a-bridge mcp-a2a-bridge
```

**Codex** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.a2a]
command = "uvx"
args = ["--from", "/Users/YOU/WorkSpace/mcp-a2a-bridge", "mcp-a2a-bridge"]
```

**Hermes:**

```bash
hermes mcp add
```

Choose a stdio server and supply the same command and args.

## Tools

| Tool | Purpose |
|---|---|
| `a2a_list_agents` | List agents with skills and reachability |
| `a2a_send_message` | Send a message; continue a task with `task_id` |
| `a2a_get_task` | Poll a task started earlier |
| `a2a_cancel_task` | Cancel a running task |
| `a2a_add_agent` | Register an agent by URL |

### Long-running tasks

`a2a_send_message` blocks up to `timeout_s` (default 60). If the agent is still
working it returns `done: false` with a `task_id`; poll `a2a_get_task`. It never
hangs the host.

### Multi-turn

An agent that returns `state: "input_required"` is answered by calling
`a2a_send_message` again with the same `task_id`.

## Dashboard

A standalone, read-only web dashboard shows configured agents' status/skills
and a live, rolling history of task activity from *every* `mcp-a2a-bridge`
process on the machine — Copilot's, Hermes', or any other MCP host's. It also
includes Hermes native A2A exchanges, including work received from other
agents. It runs as its own long-lived process, independent of any bridge, so multiple devices
on the same local network can view it at once. It uses Server-Sent Events
(SSE) to push updates: `/api/agents/events` emits `agents` events with
`{ "agents": [...] }`, and `/api/tasks/events` emits `tasks` events with
`{ "tasks": [...] }`.

Build the frontend once:

    cd dashboard
    npm install
    npm run build

Start the dashboard (once, independent of any bridge):

    mcp-a2a-bridge-dashboard

For macOS, use the included `launchd/com.example.a2a-bridge-dashboard.plist`
(template placeholders: `__REPO__` and `__HOME__`) so the dashboard is restarted
at login and after crashes. Install it as `~/Library/LaunchAgents/com.chanwit.a2a-bridge-dashboard.plist`,
then load it with `launchctl bootstrap gui/$(id -u) <plist-path>`. Do not rely
on a bridge process to host the dashboard; bridge processes are independently
started and stopped by MCP clients.

Then enable each bridge process to report into the shared activity store:

    A2A_BRIDGE_DASHBOARD=1 mcp-a2a-bridge

| Env var | Default | Purpose |
|---|---|---|
| `A2A_BRIDGE_DASHBOARD` | unset (off) | Set to `1` on a bridge or Copilot server process to persist its activity into the shared SQLite store |
| `A2A_BRIDGE_ACTIVITY_DB` | `~/.config/a2a-bridge/activity.sqlite3` | Path to the shared activity store, read by the dashboard process and written by every enabled bridge process |
| `A2A_BRIDGE_DASHBOARD_HOST` | `0.0.0.0` | Host the dashboard HTTP server binds to |
| `A2A_BRIDGE_DASHBOARD_PORT` | `9100` | Port for the dashboard HTTP server |
| `A2A_BRIDGE_HERMES_AUDIT` | unset | Read-only override for Hermes' `a2a_audit.jsonl` |
| `HERMES_HOME` | unset | When set and no override is present, reads `$HERMES_HOME/a2a_audit.jsonl` |

Visit `http://<this-machine's-IP>:9100` from any device on the same local
network. The dashboard is read-only — it never sends messages to agents.

There is no authentication: this is a local-network-trust tool, like a Vite
dev server. Do not expose it beyond a trusted LAN.

Task activity is live across every bridge process that has
`A2A_BRIDGE_DASHBOARD=1` set, because they all write into the same
`A2A_BRIDGE_ACTIVITY_DB` file and the dashboard process polls it continuously.
A bridge process without the flag set keeps its activity in memory only and
is invisible to the dashboard (today's default, zero overhead).

### Copilot A2A server

Run the consolidated Copilot server (requires an authenticated `copilot` CLI):

```sh
copilot-a2a-agent --port 9002 --cwd /path/to/default/repo
```

It binds only to `127.0.0.1` because it runs Copilot with `--allow-all-tools`.
Incoming activity uses the same opt-in shared SQLite store and is shown with
`source` and `destination` fields in the dashboard. To run it at login on
macOS, substitute `__REPO__`, `__HOME__`, and `__DEFAULT_CWD__` in
`launchd/com.example.copilot-a2a-agent.plist`, then bootstrap it with
`launchctl bootstrap gui/$(id -u) <plist-path>`.

The dashboard also polls Hermes' native audit file without modifying Hermes:
`A2A_BRIDGE_HERMES_AUDIT`, then `$HERMES_HOME/a2a_audit.jsonl`, then
`~/.hermes/a2a_audit.jsonl`. It reads only a bounded tail, skips malformed or
in-progress JSONL rows, exposes outbound calls as `hermes:<task_id>` with
`state: "recorded"`, and truncates/redacts credential-like summary fragments.
An audit record proves a call was recorded, not that its remote task completed;
inbound Hermes records and full request/response contents are intentionally not
shown.

A bridge never fails an A2A tool call because of the dashboard. If a shared
store write fails (for example two bridges contending for the SQLite file),
the bridge logs one line to stderr, pauses shared-store writes for 30s, and
then resumes on its own, so a momentary hiccup costs you a little activity
history rather than a working call.

Upgrading from an older version: bridges used to serve their own embedded
per-bridge dashboard over HTTP; they no longer do, so if you previously
visited a per-bridge dashboard URL, run `mcp-a2a-bridge-dashboard` once
instead.

## Development

```bash
.venv/bin/pytest -v
```

`tests/test_integration.py` runs a real A2A agent in-process and exercises the
bridge end to end.
