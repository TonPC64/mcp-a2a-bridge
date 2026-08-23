"""Claude Code reviewer exposed as an A2A agent on port 9010."""

from __future__ import annotations

import asyncio
import os
import shutil
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
from mcp_a2a_bridge.sqlite_task_store import SQLiteTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from fastapi import FastAPI

PORT = 9010
DEFAULT_WORKSPACE = Path.home() / "WorkSpace"
REVIEW_TIMEOUT_SECONDS = 900
HEARTBEAT_SECONDS = 15
_claude_path = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
CLAUDE_BIN = str(Path(_claude_path).expanduser().resolve())


def _process_reply(stdout: bytes, stderr: bytes, returncode: int) -> str:
    """Return useful bounded diagnostics from a Claude subprocess."""
    output = stdout.decode(errors="replace").strip()
    diagnostics = stderr.decode(errors="replace").strip()
    if returncode == 0:
        return output or diagnostics or "Claude returned no review text."
    detail = diagnostics or output or "Claude exited without an error message."
    return f"Claude exited with status {returncode}: {detail}"


def build_card(port: int = PORT) -> AgentCard:
    return AgentCard(
        name="claude-reviewer",
        description="Claude Code acting as a code reviewer",
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
        command = [
            CLAUDE_BIN,
            "-p",
            f"You are a code reviewer. {prompt}",
            "--add-dir",
            str(DEFAULT_WORKSPACE),
            "--allowedTools",
            "Read,Bash",
            "--dangerously-skip-permissions",
            "--max-turns",
            "12",
            "--model",
            "sonnet",
            "--effort",
            "medium",
            "--no-session-persistence",
        ]
        child_env = os.environ.copy()
        child_env["PATH"] = os.pathsep.join(
            [str(Path.home() / ".local" / "bin"), child_env.get("PATH", "")]
        )
        # Claude Code is running unattended under an A2A server, not in a TTY.
        # Supplying a terminal type prevents shell startup/UI helpers from
        # failing before Claude can process the review request.
        child_env.setdefault("TERM", "xterm-256color")
        child_env["CI"] = "1"
        child_env["NO_COLOR"] = "1"
        child_env["CLAUDE_CODE_NO_FLICKER"] = "1"

        # The A2A request handler already runs execute() in a producer task.
        # Keep this coroutine non-blocking and publish WORKING heartbeats so a
        # client can safely time out its stream and continue with tasks/get.
        process = None
        communicate_task = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=DEFAULT_WORKSPACE,
                env=child_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            communicate_task = asyncio.create_task(process.communicate())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + REVIEW_TIMEOUT_SECONDS

            while not communicate_task.done():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    process.kill()
                    await process.wait()
                    communicate_task.cancel()
                    try:
                        await communicate_task
                    except asyncio.CancelledError:
                        pass
                    await updater.failed(
                        updater.new_agent_message(
                            [Part(text="Claude review exceeded the 15-minute timeout.")]
                        )
                    )
                    return

                try:
                    await asyncio.wait_for(
                        asyncio.shield(communicate_task),
                        timeout=min(HEARTBEAT_SECONDS, remaining),
                    )
                except TimeoutError:
                    await updater.start_work(
                        updater.new_agent_message(
                            [Part(text="Claude review is still in progress.")]
                        )
                    )

            stdout, stderr = await communicate_task
            assert process.returncode is not None
            reply = _process_reply(stdout, stderr, process.returncode)
            event = updater.new_agent_message([Part(text=reply)])
            if process.returncode == 0:
                await updater.complete(event)
            else:
                await updater.failed(event)
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except Exception as exc:
            await updater.failed(
                updater.new_agent_message([Part(text=f"Claude reviewer failed: {exc}")])
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def build_app(card: AgentCard) -> FastAPI:
    handler = DefaultRequestHandler(
        agent_executor=ClaudeReviewerExecutor(),
        task_store=SQLiteTaskStore(
            Path.home() / ".hermes" / "a2a" / "claude-reviewer.sqlite3"
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    uvicorn.run(build_app(build_card(port)), host="127.0.0.1", port=port, log_level="info")
