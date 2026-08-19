# mcp-a2a-bridge

An MCP server that lets coding agents — GitHub Copilot CLI, Claude Code, Codex,
and Hermes — call remote [A2A](https://a2a-protocol.org) agents.

The bridge is an A2A **client**. It does not expose your coding agent as an A2A
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

## Development

```bash
.venv/bin/pytest -v
```

`tests/test_integration.py` runs a real A2A agent in-process and exercises the
bridge end to end.
