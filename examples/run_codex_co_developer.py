"""Expose Codex CLI as a local A2A co-developer."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from fastapi import FastAPI
from mcp_a2a_bridge.sqlite_task_store import SQLiteTaskStore


def build_card(port: int) -> AgentCard:
    return AgentCard(
        name="codex-co-developer",
        description="Codex CLI acting as a local coding co-developer",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://127.0.0.1:{port}/",
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="coding",
                name="Coding",
                description="Analyze, modify, and test code in the current repository",
                tags=["code", "development", "testing"],
                examples=["fix this bug", "add a unit test"],
            )
        ],
    )


class CodexExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        prompt = get_message_text(context.message)
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    shutil.which("codex") or "codex",
                    "exec",
                    "--sandbox",
                    "workspace-write",
                    "--color",
                    "never",
                    "-C",
                    str(Path(__file__).resolve().parent.parent),
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            ),
        )
        reply = result.stdout.strip() if result.returncode == 0 else f"error: {result.stderr.strip()}"
        await updater.complete(updater.new_agent_message([Part(text=reply)]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def build_app(card: AgentCard) -> FastAPI:
    handler = DefaultRequestHandler(
        agent_executor=CodexExecutor(),
        task_store=SQLiteTaskStore(
            Path.home() / ".hermes" / "a2a" / "codex-co-developer.sqlite3"
        ),
        agent_card=card,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9011
    uvicorn.run(build_app(build_card(port)), host="127.0.0.1", port=port, log_level="info")
