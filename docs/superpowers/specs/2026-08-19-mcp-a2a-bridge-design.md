# mcp-a2a-bridge — Design

**Date:** 2026-08-19
**Status:** Approved design, pending implementation plan

## Purpose

Let MCP-based coding agents — GitHub Copilot CLI, Claude Code, Codex, and Hermes —
call out to remote A2A (Agent2Agent) agents. The bridge is a stdio MCP server that
acts purely as an **A2A client**. It does not expose the host agent as an A2A server.

No maintained project does this today. The closest prior art,
`a2aproject/a2a-samples/samples/python/agents/a2a-mcp-without-framework`, runs the
opposite direction: an A2A server that consumes MCP tools.

## Scope

In scope:

- Discovering A2A agents from a shared, user-owned registry file.
- Sending messages to those agents and retrieving results, including multi-turn
  tasks and long-running tasks.
- Working identically across all four host agents.

Out of scope:

- Exposing the host as an A2A server (the "expose" direction).
- OAuth or per-request credential flows. Agents run on this machine only.
- gRPC transport. JSON-RPC over HTTP is sufficient for local agents.
- Push notification configs, `tasks/list`, and extended agent cards.

## Architecture

A standalone Python package, `mcp-a2a-bridge`, at `~/WorkSpace/mcp-a2a-bridge`.
It runs as a stdio MCP server and speaks A2A over HTTP.

```
Copilot CLI ─┐
Claude Code ─┼─ stdio/MCP ─> mcp-a2a-bridge ─ HTTP/JSON-RPC ─> A2A agents
Codex       ─┤                     │                            (localhost)
Hermes      ─┘                     └── reads ~/.config/a2a-bridge/agents.json
```

Four modules, each with one purpose and independently testable:

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Locate, load, and validate the registry file. No network. | stdlib only |
| `registry.py` | Resolve agent-card URLs to `AgentCard`s, cache them, track reachability. | `config`, `a2a-sdk` |
| `client.py` | Wrap `a2a-sdk`: send, poll, cancel. Normalize A2A types to flat dicts. | `a2a-sdk` |
| `server.py` | Declare MCP tools. Glue only, no protocol logic. | `registry`, `client` |

`server.py` holds no A2A knowledge and `client.py` holds no MCP knowledge. Either
side can be swapped without touching the other.

### Configuration

Resolution order, first match wins:

1. `$A2A_BRIDGE_CONFIG` if set.
2. `./.a2a-agents.json` in the current working directory, for project-scoped agents.
3. `~/.config/a2a-bridge/agents.json`, the shared default.

Not `~/.copilot/`: all four hosts read the same registry.

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

`url` is the agent's base URL. The bridge appends `/.well-known/agent-card.json`.
`headers` is optional and passed through on every request; it exists so a future
remote agent can be reached without a schema change, and stays empty for local use.

A missing registry file is not an error — the bridge starts with zero agents, and
`a2a_add_agent` can populate it. A malformed file **is** an error: the server fails
at startup naming the offending key, rather than silently running with a partial
registry.

### Agent card resolution

Cards are fetched lazily on first use, never at startup. A dead agent must not
prevent the host agent from starting. Once fetched, a card is cached for the
process lifetime; `a2a_list_agents(refresh=true)` clears the cache.

`a2a_list_agents` is the one exception to "errors are raised": it fetches any
uncached cards concurrently and reports per-agent `reachable` and `error` fields
rather than failing the whole call, since its purpose is to show the state of the
registry including the broken parts.

## Tool surface

Five async tools.

| Tool | Arguments | Returns |
|---|---|---|
| `a2a_list_agents` | `refresh: bool = false` | Per agent: name, description, version, url, skills, `streaming`, `reachable`, and `error` when unreachable |
| `a2a_send_message` | `agent: str`, `message: str`, `task_id: str? `, `context_id: str?`, `timeout_s: int = 60` | Terminal result text, or `{state, task_id}` when still running |
| `a2a_get_task` | `agent: str`, `task_id: str` | Current state plus text produced so far |
| `a2a_cancel_task` | `agent: str`, `task_id: str` | Final state |
| `a2a_add_agent` | `name: str`, `url: str`, `headers: dict?`, `persist: bool = true` | The resolved agent card |

### Behavior

**Multi-turn.** Passing `task_id` to `a2a_send_message` continues an existing task.
An agent that returns `input-required` is answered by calling `a2a_send_message`
again with the same `task_id`. This is the mechanism that makes conversational A2A
agents usable from a request/response tool interface.

**Streaming is internal.** `a2a-sdk` v1.x `Client.send_message()` always returns an
`AsyncIterator[StreamResponse]`, auto-dispatching to SSE or single-response based on
`ClientConfig.streaming` and `AgentCard.capabilities.streaming`. The bridge consumes
that iterator and accumulates results. The tool signature is identical either way;
the calling model never sees the difference.

**Timeout is a result, not an error.** On reaching `timeout_s` the bridge stops
consuming and returns `{state: "working", task_id}`. The host agent can then poll
with `a2a_get_task` or do other work. The bridge never blocks a host indefinitely.

**Terminal states** are `TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`,
`TASK_STATE_CANCELED`, and `TASK_STATE_REJECTED`. `TASK_STATE_INPUT_REQUIRED` and
`TASK_STATE_AUTH_REQUIRED` are *blocking, not terminal*: the bridge returns
immediately with the state and `task_id` so the caller can respond, and does not
keep waiting.

**Non-text parts** are summarized, not dumped. A `Part` with `raw` bytes becomes
`[file: <filename>, <media_type>, <n> bytes]`; a `url` part becomes
`[file: <url>, <media_type>]`; a `data` part is inlined as JSON. Base64 blobs are
never returned into the host's context window.

**`a2a_add_agent` persistence.** With `persist=true` the agent is written back to
the registry file that was resolved at startup, creating
`~/.config/a2a-bridge/agents.json` and its parent directory if no file was found.
The card is resolved before writing, so an unreachable URL is rejected rather than
persisted. With `persist=false` the agent lives only for the process lifetime.

## Data flow

`a2a_send_message("planner", "draft a plan")`:

1. `registry.get(name)` returns a cached `AgentCard`, fetching it on first use.
2. `client.send()` builds `SendMessageRequest(message=Message(role=ROLE_USER,
   parts=[Part(text=...)], message_id=uuid4()))`, plus `task_id`/`context_id` when
   continuing a task.
3. Iterate `client.send_message(request)` under an `asyncio.timeout(timeout_s)`.
   For each `StreamResponse`, dispatch on the set field: `message`, `task`,
   `status_update`, or `artifact_update`. Accumulate text; track the latest state
   and task id.
4. Stop on a terminal or blocking state, or on timeout.
5. Return the flat result to `server.py`, which hands it to the host.

## Error handling

Bridge failures are raised so the host sees a tool error. Agent outcomes are
returned as ordinary data, because a failed remote task is information, not a
bridge malfunction.

| Situation | Handling |
|---|---|
| Unknown agent name | Tool error listing valid names |
| Card fetch fails / agent unreachable | Tool error naming URL and cause; other agents unaffected |
| A2A JSON-RPC error | Tool error carrying remote code and message |
| Task ends `FAILED` / `REJECTED` | Normal return with `state` and the agent's text |
| Task blocked on `INPUT_REQUIRED` / `AUTH_REQUIRED` | Normal return with `state` and `task_id` |
| Task still working at `timeout_s` | Normal return with `state: "working"` and `task_id` |
| Malformed registry file | Fail fast at startup, naming the offending key |

Per MCP 2.0 semantics, a raised non-`MCPError` exception is returned as
`CallToolResult(is_error=True)` and is visible to the host. No silent fallbacks: an
unreachable agent is never quietly omitted from results.

## Testing

- **Unit tests per module**, with `httpx.MockTransport` as the only mocked boundary.
  Cover: config resolution order, malformed config, card caching and refresh,
  streaming and non-streaming paths, terminal vs blocking state detection, timeout
  return shape, multi-turn `task_id` continuation, and part extraction for text,
  raw, url, and data parts.
- **One integration test** against a real in-process `a2a-sdk` echo agent. This
  proves the bridge speaks actual A2A rather than our idea of it, and is the test
  that catches SDK upgrades breaking us.
- **Smoke test** that the MCP server starts over stdio and lists exactly five tools.
- Our own modules are never mocked. Mocking `client.py` inside a `server.py` test
  would only assert that we call our own code.

## Verified technical foundation

Confirmed by introspecting the installed packages, not from documentation:

| Item | Value |
|---|---|
| `a2a-sdk` | 1.1.2 (pin `>=1.1,<2`) |
| `mcp` | 2.0.0 |
| Python | >= 3.11 (`asyncio.timeout()` is 3.11+) |
| Agent card path | `/.well-known/agent-card.json` |
| Client construction | `create_client(url_or_card, client_config=..., interceptors=...)` |
| Send | `Client.send_message(SendMessageRequest) -> AsyncIterator[StreamResponse]` |
| Get / cancel | `Client.get_task(GetTaskRequest(id=...))`, `Client.cancel_task(CancelTaskRequest(id=...))` |
| Types | protobuf, from `a2a.types`: `Task`, `Message`, `Part`, `Role`, `TaskState`, `AgentCard`, `StreamResponse` |
| `Part` fields | `text`, `raw`, `url`, `data`, `metadata`, `filename`, `media_type` |
| `StreamResponse` fields | `task`, `message`, `status_update`, `artifact_update` (check with `HasField`) |
| Text extraction | `a2a.helpers.get_message_text`, `get_artifact_text`, `get_stream_response_text` |
| Custom HTTP client | `ClientConfig(httpx_client=...)` |
| MCP server class | `from mcp.server.mcpserver import MCPServer`; `@mcp.tool()`; `mcp.run(transport="stdio")` |

Two widely-documented patterns are wrong for these versions and must not be used:
`mcp.server.fastmcp.FastMCP` was removed in mcp 2.0, and `GetTaskRequest` takes
`id`, not `task_id`.

## Packaging and host registration

```toml
[project]
name = "mcp-a2a-bridge"
requires-python = ">=3.11"
dependencies = ["mcp>=2.0.0,<3", "a2a-sdk>=1.1,<2"]

[project.scripts]
mcp-a2a-bridge = "mcp_a2a_bridge.server:main"
```

Until published, hosts launch it with `uvx --from ~/WorkSpace/mcp-a2a-bridge
mcp-a2a-bridge`.

| Host | Registration |
|---|---|
| Copilot CLI | `~/.copilot/mcp-config.json` → `{"command": "uvx", "args": ["--from", "~/WorkSpace/mcp-a2a-bridge", "mcp-a2a-bridge"], "type": "stdio"}` |
| Claude Code | `claude mcp add a2a -- uvx --from ~/WorkSpace/mcp-a2a-bridge mcp-a2a-bridge` |
| Codex | `[mcp_servers.a2a]` in `~/.codex/config.toml` with the same command and args |
| Hermes | `hermes mcp add` with the same command and args |

All four share one registry, so an agent added once is visible everywhere.

## Success criteria

1. All four hosts list the five tools after registration.
2. With a sample A2A agent on localhost, `a2a_list_agents` shows its skills and
   `a2a_send_message` returns its reply.
3. A deliberately slow agent returns a `task_id` at timeout, and `a2a_get_task`
   subsequently returns the completed result.
4. An agent returning `input-required` can be driven to completion by a second
   `a2a_send_message` with the same `task_id`.
5. An unreachable agent produces a clear tool error without affecting other agents
   or preventing startup.
