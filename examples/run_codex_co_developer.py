"""Expose Codex CLI as a local A2A co-developer."""

from __future__ import annotations

import asyncio
import os
import shlex
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
CODEX_TIMEOUT_SECONDS = 600
HEARTBEAT_SECONDS = 15


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
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

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
        command = [
            CODEX_BIN,
            "exec",
            "--sandbox",
            "workspace-write",
            "--color",
            "never",
            "-C",
            str(Path(__file__).resolve().parent.parent),
            prompt,
        ]
        process = None
        communicate_task = None
        try:
            process = await asyncio.create_subprocess_exec(
                "/bin/zsh",
                "-dfc",
                f'source "$HOME/.zshrc" >/dev/null 2>&1; exec {shlex.join(command)}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
            self._processes[task.id] = process
            communicate_task = asyncio.create_task(process.communicate())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + CODEX_TIMEOUT_SECONDS

            while not communicate_task.done():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await self._stop_process(process)
                    communicate_task.cancel()
                    try:
                        await communicate_task
                    except asyncio.CancelledError:
                        pass
                    await updater.failed(
                        updater.new_agent_message(
                            [Part(text="Codex execution exceeded the 10-minute timeout.")]
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
                        updater.new_agent_message([Part(text="Codex is still working.")])
                    )

            stdout, stderr = await communicate_task
            if task.id in self._cancelled:
                return
            reply = stdout.decode().strip() if process.returncode == 0 else stderr.decode().strip()
            event = updater.new_agent_message([Part(text=reply or "Codex returned no response.")])
            if process.returncode == 0:
                await updater.complete(event)
            else:
                await updater.failed(event)
        except asyncio.CancelledError:
            if process is not None:
                await self._stop_process(process)
            raise
        except Exception as exc:
            await updater.failed(
                updater.new_agent_message([Part(text=f"Codex execution failed: {exc}")])
            )
        finally:
            self._processes.pop(task.id, None)
            self._cancelled.discard(task.id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or (context.current_task.id if context.current_task else None)
        if task_id is None:
            return
        self._cancelled.add(task_id)
        process = self._processes.get(task_id)
        if process is not None:
            await self._stop_process(process)
        task = context.current_task
        if task is not None:
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await updater.cancel(updater.new_agent_message([Part(text="Codex execution canceled.")]))


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
