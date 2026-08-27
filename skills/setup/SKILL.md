---
name: setup
description: Install and run the MCP bridge or dashboard.
version: 0.1.0
author: TonPC64, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Setup, MCP, Dashboard]
    related_skills: [contribute]
---

# Setup Skill

Use this workflow when installing or starting this project. Choose MCP-only unless the optional dashboard is needed.

## Prerequisites

- Python 3.11 or later
- `uv`
- Node.js 24.15+ and npm only for dashboard development or rebuilding dashboard assets

## Procedure

1. Clone the GitHub repository and enter it.
2. Create the environment and choose one profile:
   - MCP-only: `uv venv --python 3.11` then `uv pip install -e .`.
   - MCP + dashboard: `uv venv --python 3.11` then `uv pip install -e ".[dashboard]"`.
3. Configure agents in `.a2a-agents.json` or set `A2A_BRIDGE_CONFIG` to an absolute registry path. Treat headers as secrets and never commit the registry.
4. Register `uv run --directory /absolute/path/to/mcp-a2a-bridge mcp-a2a-bridge` with the selected MCP host.
5. For the dashboard profile, run `npm --prefix dashboard ci`, `npm --prefix dashboard run build`, then `uv run mcp-a2a-bridge-dashboard`.
6. Enable activity reporting with `A2A_BRIDGE_DASHBOARD=1` on each bridge process that should appear.
7. Use loopback by default. For LAN access, explicitly set `A2A_BRIDGE_DASHBOARD_HOST`, set `A2A_BRIDGE_DASHBOARD_TOKEN`, and put the service behind TLS and network controls.

## Verification

- `uv lock --check` succeeds.
- `uv run mcp-a2a-bridge` starts without dashboard dependencies in MCP-only mode.
- Dashboard profile builds and serves `127.0.0.1:9100`.
- The dashboard token protects HTML, static assets, API, and SSE routes.

## Pitfalls

- MCP-only does not need Node/npm.
- Do not bind the unauthenticated dashboard to a non-loopback address.
- Build the frontend after source changes; the Python service serves `dashboard/dist`.
- Do not copy personal paths or real tokens from examples.
