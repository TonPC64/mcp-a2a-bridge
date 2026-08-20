"""Claude Code reviewer exposed as an A2A agent on port 9010."""

from __future__ import annotations

import asyncio
import shlex
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
from mcp_a2a_bridge.ttl_task_store import TTLTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from fastapi import FastAPI

PORT = 9010
DEFAULT_WORKSPACE = Path.home() / "WorkSpace"
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def build_card() -> AgentCard:
    return AgentCard(
        name="claude-reviewer",
        description="Claude Code acting as a code reviewer",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://127.0.0.1:{PORT}/",
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="code-review",
                name="Code Review",
                description="Review code, diffs, or pull requests for correctness, style, and bugs",
                tags=["review", "code", "diff", "pr"],
                examples=["review this diff", "check my code for bugs"],
            )
        ],
    )


class ClaudeReviewerExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        prompt = get_message_text(context.message)
        # ponytail: subprocess claude -p — no SDK wrapper needed for a local CLI call
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    "/bin/zsh",
                    "-lic",
                    shlex.join(
                        [
                            "exec",
                            CLAUDE_BIN,
                            "-p",
                            f"You are a code reviewer. {prompt}",
                            "--add-dir",
                            str(DEFAULT_WORKSPACE),
                            "--allowedTools",
                            "Read,Bash",
                            "--dangerously-skip-permissions",
                            "--max-turns",
                            "5",
                        ]
                    ),
                ],
                capture_output=True,
                text=True,
                cwd=DEFAULT_WORKSPACE,
                timeout=300,
            ),
        )
        reply = result.stdout.strip() if result.returncode == 0 else f"error: {result.stderr.strip()}"
        await updater.complete(updater.new_agent_message([Part(text=reply)]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def build_app(card: AgentCard) -> FastAPI:
    handler = DefaultRequestHandler(
        agent_executor=ClaudeReviewerExecutor(),
        task_store=TTLTaskStore(),
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    uvicorn.run(build_app(build_card()), host="127.0.0.1", port=port, log_level="info")
