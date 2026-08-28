"""FastAPI app and entry point for the Copilot A2A agent."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from fastapi import FastAPI

from copilot_a2a_agent.card import build_card
from copilot_a2a_agent.executor import CopilotExecutor

DEFAULT_PORT = 9010


def build_app(card: AgentCard, default_cwd: str, timeout_s: float = 1800) -> FastAPI:
    handler = DefaultRequestHandler(agent_executor=CopilotExecutor(default_cwd, timeout_s), task_store=InMemoryTaskStore(), agent_card=card)
    app = FastAPI()
    add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card), jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"))
    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="copilot-a2a-agent")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    uvicorn.run(build_app(build_card(args.port), str(Path(args.cwd).expanduser().resolve()), args.timeout), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
