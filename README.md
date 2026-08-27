# mcp-a2a-bridge

An MCP server that lets coding agents — GitHub Copilot CLI, Claude Code, Codex,
and Hermes — call remote [A2A](https://a2a-protocol.org) agents. It also
includes an optional read-only activity dashboard.

Repository: https://github.com/TonPC64/mcp-a2a-bridge

The GitHub repository is currently private while the project is being prepared
for public release.

## Getting started

## Choose your setup

Both profiles need Python 3.11 or later and [`uv`](https://docs.astral.sh/uv/).

### MCP only

Install and run the MCP bridge only. This does not require Node.js/npm or a
dashboard frontend build.

```bash
git clone https://github.com/TonPC64/mcp-a2a-bridge.git mcp-a2a-bridge
cd mcp-a2a-bridge
uv venv --python 3.11
uv pip install -e .
```

### MCP + dashboard

Install the optional dashboard web runtime, then build its bundled frontend
assets. Node.js and npm are needed only for this build (or frontend
development), not to run an already-built dashboard.

```bash
git clone https://github.com/TonPC64/mcp-a2a-bridge.git mcp-a2a-bridge
cd mcp-a2a-bridge
uv venv --python 3.11
uv pip install -e ".[dashboard]"
npm --prefix dashboard ci
npm --prefix dashboard run build
```

For development and tests, install `.[dev]`; it includes the Python packages
needed by the dashboard test suite. The commands below use
`/absolute/path/to/mcp-a2a-bridge`. Replace it with the absolute path to your
clone; do not copy another user's path.

### Configure A2A agents

Create `.a2a-agents.json` in the repository root:

```json
{
  "agents": {
    "example-agent": {
      "url": "https://agent.example.invalid",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_YOUR_TOKEN"
      }
    }
  }
}
```

Use the base URL of an A2A agent; the bridge requests its
`/.well-known/agent-card.json` automatically. Remove `Authorization` (or use
an empty `headers` object) when the agent does not require authentication.
The registry can contain secrets, so do not commit it and restrict its file
permissions where appropriate.

The registry location is resolved in this order:

1. `A2A_BRIDGE_CONFIG`
2. `.a2a-agents.json` in the bridge process's current directory
3. `~/.config/a2a-bridge/agents.json`

For a host that starts the MCP command outside the repository, set
`A2A_BRIDGE_CONFIG` to an absolute path in that host's MCP environment. You
can also add a verified agent at runtime with `a2a_add_agent`; by default it
writes to the resolved registry file.

### Register the MCP server

Each host should start the same local checkout command:

```text
uv run --directory /absolute/path/to/mcp-a2a-bridge mcp-a2a-bridge
```

This uses the editable installation created above. Configure the host with
the following portable path placeholder.

**GitHub Copilot CLI** — add this server to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "a2a": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/mcp-a2a-bridge",
        "mcp-a2a-bridge"
      ]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add a2a -- uv run --directory /absolute/path/to/mcp-a2a-bridge mcp-a2a-bridge
```

**Codex** — add this to `~/.codex/config.toml`:

```toml
[mcp_servers.a2a]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/mcp-a2a-bridge", "mcp-a2a-bridge"]
```

**Hermes:** use its MCP server registration UI or command, choose a `stdio`
server, and supply the command `uv` with these arguments:

```text
run --directory /absolute/path/to/mcp-a2a-bridge mcp-a2a-bridge
```

Restart the host after registration, then call `a2a_list_agents` to confirm
that the registry is available and the agents are reachable.

## Tools

| Tool | Purpose |
|---|---|
| `a2a_list_agents` | List agents with skills and reachability |
| `a2a_send_message` | Send a message; continue a task with `task_id` |
| `a2a_get_task` | Poll a task started earlier |
| `a2a_cancel_task` | Cancel a running task |
| `a2a_add_agent` | Register an agent by URL |

`a2a_send_message` waits up to `timeout_s` (60 seconds by default). If a task
is still running, it returns `done: false` with a `task_id`; poll it with
`a2a_get_task`. If an agent returns `state: "input_required"`, call
`a2a_send_message` again with the same `task_id`.

## Dashboard

The optional standalone dashboard shows configured agents, their
status/skills, and live task activity from bridge processes on the same
machine. It is read-only: it never sends messages to agents.

Install the `dashboard` profile and build the assets before starting it:

```bash
cd /absolute/path/to/mcp-a2a-bridge
uv pip install -e ".[dashboard]"
npm --prefix dashboard ci
npm --prefix dashboard run build
uv run mcp-a2a-bridge-dashboard
```

It listens on port `9100` by default. Open `http://127.0.0.1:9100` on the
same machine, or `http://<machine-ip>:9100` from a trusted local network.

Enable reporting for every bridge process whose activity should appear in the
dashboard. Add this environment variable to the MCP server configuration (or
when launching it manually):

```bash
A2A_BRIDGE_DASHBOARD=1 uv run --directory /absolute/path/to/mcp-a2a-bridge mcp-a2a-bridge
```

Bridge processes without that flag keep activity in memory and do not appear
in the shared dashboard. The dashboard can start before or after them.

| Environment variable | Default | Purpose |
|---|---|---|
| `A2A_BRIDGE_CONFIG` | — | Absolute path to the agents JSON registry |
| `A2A_BRIDGE_DASHBOARD` | unset (off) | Set to `1` to persist this bridge process's activity |
| `A2A_BRIDGE_ACTIVITY_DB` | `~/.config/a2a-bridge/activity.sqlite3` | Shared SQLite activity database |
| `A2A_BRIDGE_ACTIVITY_SOURCE` | `remote` | Source label for activity written by compatible processes |
| `A2A_BRIDGE_DASHBOARD_HOST` | `0.0.0.0` | Dashboard bind address |
| `A2A_BRIDGE_DASHBOARD_PORT` | `9100` | Dashboard port |
| `A2A_BRIDGE_HERMES_AUDIT` | unset | Read-only override for Hermes' `a2a_audit.jsonl` |
| `HERMES_HOME` | unset | Used to find `$HERMES_HOME/a2a_audit.jsonl` when no override is set |

**Security warning:** the dashboard is currently unauthenticated and, by
default, binds to all network interfaces. Treat it as local-network-only. Do
not expose it to the public internet without authentication, TLS, and network
access controls. For a single-machine setup, bind it to loopback with
`A2A_BRIDGE_DASHBOARD_HOST=127.0.0.1`.

For macOS, the `launchd/com.example.a2a-bridge-dashboard.plist` template can
run the dashboard at login. Replace its `__REPO__` and `__HOME__` placeholders
before installing it as a LaunchAgent.

### Dashboard frontend development

Node.js/npm are only required to work on the frontend or rebuild its bundled
assets:

```bash
cd /absolute/path/to/mcp-a2a-bridge/dashboard
npm ci
npm run dev
```

Vite prints the development URL (normally `http://localhost:5173`). Build the
assets consumed by the Python dashboard with `npm run build`.

## Copilot A2A server

The bundled server requires an authenticated `copilot` CLI:

```bash
copilot-a2a-agent --port 9002 --cwd /path/to/default/repository
```

It binds to `127.0.0.1` because it runs Copilot with `--allow-all-tools`.
Its incoming activity can use the same opt-in shared SQLite store and is
shown with source and destination fields in the dashboard.

The dashboard can also read Hermes' native audit file without modifying
Hermes. It checks `A2A_BRIDGE_HERMES_AUDIT`, then
`$HERMES_HOME/a2a_audit.jsonl`, then `~/.hermes/a2a_audit.jsonl`.

## Development and tests

Run the Python test suite from the repository root:

```bash
uv run pytest -v
```

Run dashboard tests or make a production dashboard build from `dashboard/`:

```bash
npm test
npm run build
```

`tests/test_integration.py` runs an A2A agent in-process and exercises the
bridge end to end.
