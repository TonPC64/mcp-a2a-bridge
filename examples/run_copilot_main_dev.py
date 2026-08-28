"""Expose GitHub Copilot CLI as an optional local A2A agent example."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part, TaskState
from fastapi import FastAPI

from mcp_a2a_bridge.activity_writer import build_activity_writer

COPILOT_BIN = "copilot"
DEFAULT_PORT = 9010
_SESSION_NAMESPACE = uuid.UUID("6f2b1c58-0c2b-4c2e-9a1a-0b6d9a4f1e21")
CWD_PREFIX = "cwd:"
log = logging.getLogger(__name__)


class RunnerError(Exception):
    """Copilot could not be run at all."""


@dataclass
class CopilotResult:
    text: str = ""
    session_id: str = ""
    exit_code: int | None = None
    files_modified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def session_uuid(context_id: str) -> str:
    try:
        return str(uuid.UUID(context_id))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(_SESSION_NAMESPACE, str(context_id)))


def split_cwd(text: str, default_cwd: str) -> tuple[str, str]:
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith(CWD_PREFIX):
        cwd = lines[0].strip()[len(CWD_PREFIX):].strip()
        return "\n".join(lines[1:]).lstrip("\n"), cwd or default_cwd
    return text, default_cwd


def build_argv(prompt: str, cwd: str, session_id: str) -> list[str]:
    return [COPILOT_BIN, "-p", prompt, "--output-format", "json", "--allow-all-tools", "--no-color", "-C", cwd, "--session-id", session_id]


def parse_line(line: str) -> dict | None:
    try:
        event = json.loads(line) if line.strip().startswith("{") else None
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def event_progress(event: dict) -> str | None:
    data = event.get("data") or {}
    if event.get("type") == "tool.execution_start":
        return f"running {data.get('toolName') or data.get('name') or 'tool'}"
    if event.get("type") == "assistant.turn_start":
        return "thinking"
    return None


def apply_event(event: dict, result: CopilotResult) -> CopilotResult:
    data = event.get("data") or {}
    if event.get("type") == "assistant.message" and data.get("content"):
        result.text = data["content"]
    elif event.get("type") == "result":
        result.session_id = event.get("sessionId") or result.session_id
        result.exit_code = event.get("exitCode")
        result.files_modified = list(((event.get("usage") or {}).get("codeChanges") or {}).get("filesModified") or [])
    return result


def provider_environment(provider: str | None) -> dict[str, str]:
    env = os.environ.copy()
    names = ("COPILOT_PROVIDER_BASE_URL", "COPILOT_PROVIDER_TYPE", "COPILOT_PROVIDER_API_KEY", "COPILOT_PROVIDER_BEARER_TOKEN", "COPILOT_PROVIDER_WIRE_API", "COPILOT_PROVIDER_MODEL_ID", "COPILOT_PROVIDER_WIRE_MODEL")
    if provider == "github":
        for name in names:
            env.pop(name, None)
    elif provider == "litellm-auto":
        env.update({"COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:4000/v1", "COPILOT_PROVIDER_TYPE": "openai", "COPILOT_PROVIDER_API_KEY": env.get("LITELLM_MASTER_KEY", ""), "COPILOT_PROVIDER_WIRE_API": "responses", "COPILOT_PROVIDER_MODEL_ID": "gpt-5.4", "COPILOT_PROVIDER_WIRE_MODEL": "auto"})
    return env


async def run_copilot(prompt: str, cwd: str, session_id: str, timeout_s: float = 1800, provider: str | None = None) -> AsyncIterator[tuple[dict, CopilotResult]]:
    if not Path(cwd).is_dir():
        raise RunnerError(f"working directory does not exist: {cwd}")
    try:
        proc = await asyncio.create_subprocess_exec(*build_argv(prompt, cwd, session_id), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=provider_environment(provider))
    except FileNotFoundError as exc:
        raise RunnerError(f"{COPILOT_BIN} not found on PATH") from exc
    stderr_task = asyncio.create_task(proc.stderr.read()) if proc.stderr else None
    result = CopilotResult(session_id=session_id)
    try:
        async with asyncio.timeout(timeout_s):
            assert proc.stdout is not None
            buffer = b""
            while chunk := await proc.stdout.read(64 * 1024):
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    if event := parse_line(raw.decode("utf-8", "replace")):
                        yield event, apply_event(event, result)
            if buffer and (event := parse_line(buffer.decode("utf-8", "replace"))):
                yield event, apply_event(event, result)
    except TimeoutError:
        result.exit_code = result.exit_code if result.exit_code is not None else -1
        result.text = result.text or f"Copilot timed out after {timeout_s:.0f}s."
        raise
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        if stderr_task:
            await stderr_task


def build_card(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> AgentCard:
    return AgentCard(
        name="copilot_main_dev",
        description="GitHub Copilot CLI — a coding agent that can read and edit files and run commands.",
        version="0.1.0",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=f"http://{host}:{port}/")],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(id="delegate-coding-task", name="Delegate a coding task", description="Ask Copilot to work in a local repository. Prefix with 'cwd: /path/to/repo' to choose it. Prefix with 'provider: litellm-auto' or 'provider: github' to select the model provider.", tags=["coding", "refactor", "tests", "shell", "repository"], examples=["cwd: /Users/me/project\nAdd a --verbose flag to the CLI and test it", "cwd: /Users/me/project\nWhy is test_auth failing?"])],
    )


def format_reply(result: CopilotResult) -> str:
    text = result.text.strip() or "(Copilot produced no message.)"
    return f"{text}\n\nFiles modified:\n" + "\n".join(f"- {path}" for path in result.files_modified) if result.files_modified else text


class CopilotExecutor(AgentExecutor):
    def __init__(self, default_cwd: str, timeout_s: float = 1800) -> None:
        self._default_cwd, self._timeout_s = default_cwd, timeout_s
        self._activity = build_activity_writer("copilot_main_dev")

    def _record(self, task_id: str, *, state: str, text: str, source: str = "remote") -> None:
        if self._activity:
            self._activity.record(task_id, source=source, state=state, text=text)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        source = self._activity.source_for(context) if self._activity else "remote"
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        self._record(task.id, source=source, state="working", text="Task received by Copilot.")
        provider = None
        prompt = get_message_text(context.message)
        lines = prompt.splitlines()
        if lines and lines[0].strip().lower().startswith("provider:"):
            provider = lines[0].split(":", 1)[1].strip().lower()
            prompt = "\n".join(lines[1:]).lstrip("\n")
            if provider not in {"litellm-auto", "github"}:
                await updater.failed(updater.new_agent_message([Part(text="Unsupported provider. Use litellm-auto or github.")]))
                self._record(task.id, source=source, state="failed", text="Unsupported provider.")
                return
        prompt, cwd = split_cwd(prompt, self._default_cwd)
        if not prompt.strip():
            await updater.failed(updater.new_agent_message([Part(text="No instruction was provided.")]))
            self._record(task.id, source=source, state="failed", text="No instruction was provided.")
            return
        result = CopilotResult(session_id=session_uuid(task.context_id))
        last_note = ""
        try:
            async for event, result in run_copilot(prompt, cwd, result.session_id, timeout_s=self._timeout_s, provider=provider):
                if (note := event_progress(event)) and note != last_note:
                    last_note = note
                    await updater.update_status(TaskState.TASK_STATE_WORKING, updater.new_agent_message([Part(text=note)]))
                    self._record(task.id, source=source, state="working", text=note)
        except RunnerError as exc:
            reply = f"Cannot run Copilot: {exc}"
        except TimeoutError:
            reply = f"Copilot timed out after {self._timeout_s:.0f}s."
        except Exception as exc:
            log.exception("copilot run failed")
            reply = f"Copilot run failed: {exc}"
        else:
            reply = format_reply(result)
            if result.ok:
                await updater.complete(updater.new_agent_message([Part(text=reply)]))
                self._record(task.id, source=source, state="completed", text=reply)
                return
        await updater.failed(updater.new_agent_message([Part(text=reply)]))
        self._record(task.id, source=source, state="failed", text=reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def build_app(card: AgentCard, default_cwd: str, timeout_s: float = 1800) -> FastAPI:
    handler = DefaultRequestHandler(agent_executor=CopilotExecutor(default_cwd, timeout_s), task_store=InMemoryTaskStore(), agent_card=card)
    app = FastAPI()
    add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card), jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"))
    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_copilot_main_dev")
    parser.add_argument("port", nargs="?", type=int)
    parser.add_argument("--port", dest="port_option", type=int)
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    port = args.port_option or args.port or DEFAULT_PORT
    uvicorn.run(build_app(build_card(port), str(Path(args.cwd).expanduser().resolve()), args.timeout), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
