# AI README

This file is the fast-start guide for coding agents working on `mcp-a2a-bridge`.
Read it before changing code, then read the relevant skill:

- `skills/setup/SKILL.md` — install and run MCP-only or MCP + dashboard.
- `skills/contribute/SKILL.md` — branch, test, security review, commit, and PR workflow.

## Project map

- `src/mcp_a2a_bridge/` — core MCP bridge, A2A client, registry, activity store, optional dashboard backend.
- `src/copilot_a2a_agent/` — bundled Copilot A2A server.
- `dashboard/` — optional React/Vite dashboard frontend.
- `tests/` — Python tests, including integration coverage.
- `.github/workflows/ci.yml` — CI for Python and dashboard.

## Choose a setup

MCP-only (no dashboard runtime or Node/npm):

```bash
uv venv --python 3.11
uv pip install -e .
uv run mcp-a2a-bridge
```

MCP + dashboard:

```bash
uv venv --python 3.11
uv pip install -e ".[dashboard]"
npm --prefix dashboard ci
npm --prefix dashboard run build
uv run mcp-a2a-bridge-dashboard
```

Set `A2A_BRIDGE_DASHBOARD=1` on bridge processes whose activity should appear in the dashboard. The dashboard defaults to `127.0.0.1:9100`. A non-loopback bind without a token is for trusted LANs only; use a strong `A2A_BRIDGE_DASHBOARD_TOKEN`, TLS, and network controls for protected access. Never expose the dashboard directly to the public internet.

For frontend-only screenshots, run `npm --prefix dashboard ci` then
`npm --prefix dashboard run dev:mock -- --host 0.0.0.0`. This opt-in mode uses
local mock snapshots instead of the dashboard API/SSE; normal builds stay live.

## Agent rules

1. Read `AGENTS.md` and the relevant skill before editing.
2. Preserve MCP-only installs: dashboard dependencies belong in the `dashboard` extra.
3. Do not commit credentials, local registry files, `.venv`, `node_modules`, or generated screenshots.
4. Prefer small, tested changes; preserve existing APIs and SSE contracts.
5. Run the verification commands in `skills/contribute/SKILL.md` before reporting completion.
6. Do not claim a browser/CDP check, test, commit, push, or PR exists unless it actually ran.
