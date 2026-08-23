"""Expose Codex CLI as a local A2A co-developer."""

from __future__ import annotations

import asyncio
import os
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


# Common install locations for the codex CLI that a login shell's PATH would
# include but a launchd service's minimal PATH ("/usr/bin:/bin:/usr/sbin:/sbin")
# does not — notably Homebrew's bin directory, where `brew install codex`
# places a symlink. Checked in order after shutil.which finds nothing.
_CODEX_SEARCH_DIRS = (
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path.home() / ".local" / "bin",
)


def _resolve_codex_bin() -> str:
    """Find the codex CLI's absolute path even under a minimal PATH.

    This process is often launched by launchd (e.g. as a LaunchAgent), whose
    PATH is a minimal `/usr/bin:/bin:/usr/sbin:/sbin` — it does not include
    Homebrew's `/opt/homebrew/bin`, npm's global bin, or any other directory
    a login shell would pick up from the user's shell rc files. `shutil.which`
    against that minimal PATH silently returns None, and subprocess then
    fails with "No such file or directory: 'codex'". Checking a fixed list of
    common install directories (the same non-interactive approach the Claude
    reviewer in examples/run_claude_reviewer.py uses for CLAUDE_BIN) makes
    discovery independent of how this process itself was started, without
    spawning an interactive login shell.
    """
    found = shutil.which("codex")
    if found:
        return found

    for directory in _CODEX_SEARCH_DIRS:
        candidate = directory / "codex"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return "codex"


CODEX_BIN = _resolve_codex_bin()


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
        child_env = os.environ.copy()
        child_env["PATH"] = os.pathsep.join(
            [str(directory) for directory in _CODEX_SEARCH_DIRS] + [child_env.get("PATH", "")]
        )
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    CODEX_BIN,
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
                env=child_env,
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
