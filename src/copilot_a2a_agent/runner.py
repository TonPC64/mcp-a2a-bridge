"""Run Copilot CLI non-interactively and parse its JSONL output."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

COPILOT_BIN = "copilot"
_SESSION_NAMESPACE = uuid.UUID("6f2b1c58-0c2b-4c2e-9a1a-0b6d9a4f1e21")
CWD_PREFIX = "cwd:"


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


async def run_copilot(prompt: str, cwd: str, session_id: str, timeout_s: float = 1800) -> AsyncIterator[tuple[dict, CopilotResult]]:
    if not Path(cwd).is_dir():
        raise RunnerError(f"working directory does not exist: {cwd}")
    try:
        proc = await asyncio.create_subprocess_exec(*build_argv(prompt, cwd, session_id), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RunnerError(f"{COPILOT_BIN} not found on PATH") from exc
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
