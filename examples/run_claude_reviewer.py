"""Claude Code reviewer exposed as an A2A agent on port 9010."""

from __future__ import annotations

import asyncio
import os
import re
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
from mcp_a2a_bridge.activity_writer import ActivityWriter, build_activity_writer
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from fastapi import FastAPI

PORT = 9010
DEFAULT_WORKSPACE = Path.home() / "WorkSpace"
REVIEW_TIMEOUT_SECONDS = 900
HEARTBEAT_SECONDS = 15
REVIEW_BUDGET_INSTRUCTIONS = (
    "Apply a strict review budget: inspect the PR metadata, the PR diff, and only "
    "the directly affected files and tests. Do not perform broad repository searches, "
    "read unrelated documentation, or run the full test suite. Use at most eight "
    "tool turns and return the final review immediately once the diff is understood."
)
MAX_PREFLIGHT_CHARS = 120_000
PR_URL_PATTERN = re.compile(
    r"https://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
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


def _pr_reference(prompt: str) -> tuple[str, str, str, str] | None:
    match = PR_URL_PATTERN.search(prompt)
    if match is None:
        return None
    return match.group("host"), match.group("owner"), match.group("repo"), match.group("number")


async def _run_preflight(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return ""
    return stdout.decode(errors="replace").strip()


async def _preflight_prompt(prompt: str) -> tuple[str, bool]:
    reference = _pr_reference(prompt)
    if reference is None:
        return prompt, False
    host, owner, repo, number = reference
    repository = re.search(r"Repository:\s*(\S+)", prompt)
    cwd = Path(repository.group(1).rstrip(".,;:")).expanduser() if repository else DEFAULT_WORKSPACE
    if not cwd.is_dir():
        return prompt, False
    env = os.environ.copy()
    env["GH_HOST"] = host
    metadata = await _run_preflight(
        [
            "gh", "pr", "view", number, "--repo", f"{owner}/{repo}", "--json",
            "title,body,state,isDraft,baseRefName,headRefName,headRefOid,changedFiles,additions,deletions",
        ],
        cwd,
        env,
    )
    diff = await _run_preflight(
        ["gh", "pr", "diff", number, "--repo", f"{owner}/{repo}"], cwd, env
    )
    guidance_path = cwd / "AGENTS.md"
    guidance = guidance_path.read_text(errors="replace") if guidance_path.is_file() else ""
    if not metadata or not diff:
        return prompt, False
    context = "\n\n".join(
        (
            "PR PREFLIGHT METADATA:\n" + metadata,
            "REPOSITORY GUIDANCE (root AGENTS.md):\n" + guidance[:20_000],
            "PR DIFF:\n" + diff[:MAX_PREFLIGHT_CHARS],
        )
    )
    if len(diff) > MAX_PREFLIGHT_CHARS:
        context += "\n[PR diff truncated by preflight; review the supplied portion only.]"
    return (
        prompt
        + "\n\nThe bridge already collected the bounded PR context below. Review this supplied context directly; "
        + "do not re-fetch the PR or perform broad repository exploration.\n\n"
        + context,
        True,
    )


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
    def __init__(self, activity_writer: ActivityWriter | None = None) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._activity_writer = activity_writer if activity_writer is not None else build_activity_writer("claude-reviewer")

    def _record_activity(self, task_id: str, source: str, state: str, text: str) -> None:
        if self._activity_writer is None:
            return
        try:
            self._activity_writer.record(task_id, source=source, state=state, text=text)
        except Exception:
            pass

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        source = self._activity_writer.source_for(context) if self._activity_writer else "remote"
        self._record_activity(task.id, source, "working", "Task received.")

        prompt, preflighted = await _preflight_prompt(get_message_text(context.message))
        command = [
            CLAUDE_BIN,
            "--bare",
            "--tools",
            "Read" if preflighted else "Read,Bash",
            "--append-system-prompt",
            REVIEW_BUDGET_INSTRUCTIONS,
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
            self._processes[task.id] = process
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
                    self._record_activity(task.id, source, "failed", "Claude review exceeded the timeout.")
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
                    self._record_activity(task.id, source, "working", "Claude review is still in progress.")

            stdout, stderr = await communicate_task
            if task.id in self._cancelled:
                return
            assert process.returncode is not None
            reply = _process_reply(stdout, stderr, process.returncode)
            event = updater.new_agent_message([Part(text=reply)])
            if process.returncode == 0:
                await updater.complete(event)
                self._record_activity(task.id, source, "completed", reply)
            else:
                await updater.failed(event)
                self._record_activity(task.id, source, "failed", reply)
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except Exception as exc:
            await updater.failed(
                updater.new_agent_message([Part(text=f"Claude reviewer failed: {exc}")])
            )
            self._record_activity(task.id, source, "failed", f"Claude reviewer failed: {exc}")
        finally:
            self._processes.pop(task.id, None)
            self._cancelled.discard(task.id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or (context.current_task.id if context.current_task else None)
        if task_id is None:
            return
        self._cancelled.add(task_id)
        process = self._processes.get(task_id)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        task = context.current_task
        if task is not None:
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await updater.cancel(updater.new_agent_message([Part(text="Claude review canceled.")]))
            source = self._activity_writer.source_for(context) if self._activity_writer else "remote"
            self._record_activity(task.id, source, "canceled", "Claude review canceled.")


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
