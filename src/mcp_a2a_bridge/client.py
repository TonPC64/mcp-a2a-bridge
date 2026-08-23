"""A2A client calls and protobuf-to-plain-data normalization."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import new_text_message
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
)
from google.protobuf.json_format import MessageToJson

from mcp_a2a_bridge.config import AgentEntry

TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}

BLOCKING_STATES = {
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}


def state_name(state: int) -> str:
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


def is_done(state: int) -> bool:
    """Stop consuming: the task is finished or is waiting on the caller."""
    return state in TERMINAL_STATES or state in BLOCKING_STATES


def summarize_part(part: Part) -> str:
    # The Part oneof is named "content", not "part".
    which = part.WhichOneof("content")

    if which == "text":
        return part.text

    if which == "raw":
        label = part.filename or "unnamed"
        media = part.media_type or "application/octet-stream"
        return f"[file: {label}, {media}, {len(part.raw)} bytes]"

    if which == "url":
        media = part.media_type or "application/octet-stream"
        return f"[file: {part.url}, {media}]"

    if which == "data":
        return MessageToJson(part.data, indent=0).replace("\n", "")

    return ""


def card_summary(card: AgentCard) -> dict:
    url = None
    if card.supported_interfaces:
        url = card.supported_interfaces[0].url

    return {
        "name": card.name,
        "description": card.description,
        "version": card.version,
        "url": url,
        "streaming": bool(card.capabilities.streaming),
        "input_modes": list(card.default_input_modes),
        "output_modes": list(card.default_output_modes),
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "examples": list(skill.examples),
            }
            for skill in card.skills
        ],
    }


@dataclass
class A2AResult:
    state: str
    text: str
    task_id: str | None
    context_id: str | None
    done: bool

    def to_dict(self) -> dict:
        result: dict = {"state": self.state, "text": self.text, "done": self.done}
        if self.task_id:
            result["task_id"] = self.task_id
        if self.context_id:
            result["context_id"] = self.context_id
        return result


def _message_text(message: Message) -> str:
    return "\n".join(
        summary for summary in (summarize_part(p) for p in message.parts) if summary
    )


def _task_result(task: Task) -> A2AResult:
    text = _message_text(task.status.message) if task.status.HasField("message") else ""
    if not text:
        pieces = []
        for artifact in task.artifacts:
            for part in artifact.parts:
                summary = summarize_part(part)
                if summary:
                    pieces.append(summary)
        text = "\n".join(pieces)

    return A2AResult(
        state=state_name(task.status.state),
        text=text,
        task_id=task.id or None,
        context_id=task.context_id or None,
        done=is_done(task.status.state),
    )


async def consume_stream(
    chunks: AsyncIterator[StreamResponse],
    task_id: str | None,
    context_id: str | None,
    timeout_s: float = 60,
    on_update: Callable[[A2AResult], Awaitable[None]] | None = None,
) -> A2AResult:
    """Accumulate a StreamResponse iterator into one flat result.

    A terminal status message is the agent's final answer, so it replaces the
    intermediate progress notes rather than being appended to them.

    Returns rather than raises on timeout: a still-running task is a normal
    outcome the caller can poll on.
    """
    pieces: list[str] = []
    final_text = ""
    state: int | None = None
    timed_out = False

    async def publish() -> None:
        if on_update is None or state is None:
            return
        await on_update(
            A2AResult(
                state=state_name(state),
                text=final_text or "\n".join(pieces),
                task_id=task_id,
                context_id=context_id,
                done=is_done(state),
            )
        )

    def record(status) -> None:
        """Route a status message: a terminal one is the answer, others are progress."""
        nonlocal final_text
        if not status.HasField("message"):
            return
        text = _message_text(status.message)
        if not text:
            return
        if is_done(status.state):
            final_text = text
        else:
            pieces.append(text)

    try:
        async with asyncio.timeout(timeout_s):
            async for chunk in chunks:
                which = chunk.WhichOneof("payload")

                if which == "task":
                    task_id = chunk.task.id or task_id
                    context_id = chunk.task.context_id or context_id
                    state = chunk.task.status.state
                    record(chunk.task.status)

                elif which == "status_update":
                    update = chunk.status_update
                    task_id = update.task_id or task_id
                    context_id = update.context_id or context_id
                    state = update.status.state
                    record(update.status)

                elif which == "artifact_update":
                    update = chunk.artifact_update
                    task_id = update.task_id or task_id
                    context_id = update.context_id or context_id
                    for part in update.artifact.parts:
                        text = summarize_part(part)
                        if text:
                            pieces.append(text)

                elif which == "message":
                    text = _message_text(chunk.message)
                    if text:
                        pieces.append(text)
                    task_id = chunk.message.task_id or task_id
                    context_id = chunk.message.context_id or context_id
                    if state is None:
                        state = TaskState.TASK_STATE_COMPLETED

                await publish()

                if state is not None and is_done(state):
                    break
    except TimeoutError:
        timed_out = True
    finally:
        aclose = getattr(chunks, "aclose", None)
        if aclose is not None:
            await aclose()

    if timed_out:
        state = TaskState.TASK_STATE_WORKING
    elif state is None:
        state = TaskState.TASK_STATE_COMPLETED

    return A2AResult(
        state=state_name(state),
        text=final_text or "\n".join(pieces),
        task_id=task_id,
        context_id=context_id,
        done=is_done(state),
    )


def _http_client(entry: AgentEntry) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=entry.headers, timeout=None)


def _build_client(http_client: httpx.AsyncClient, card: AgentCard):
    config = ClientConfig(
        httpx_client=http_client,
        accepted_output_modes=["text/plain"],
    )
    return ClientFactory(config=config).create(card)


async def send_message(
    entry: AgentEntry,
    card: AgentCard,
    message: str,
    task_id: str | None = None,
    context_id: str | None = None,
    timeout_s: int = 60,
    on_update: Callable[[A2AResult], Awaitable[None]] | None = None,
) -> A2AResult:
    async with _http_client(entry) as http_client:
        client = _build_client(http_client, card)
        request = SendMessageRequest(
            message=new_text_message(
                message,
                role=Role.ROLE_USER,
                task_id=task_id,
                context_id=context_id,
            )
        )
        return await consume_stream(
            client.send_message(request),
            task_id=task_id,
            context_id=context_id,
            timeout_s=timeout_s,
            on_update=on_update,
        )


async def get_task(entry: AgentEntry, card: AgentCard, task_id: str) -> A2AResult:
    async with _http_client(entry) as http_client:
        client = _build_client(http_client, card)
        task = await client.get_task(GetTaskRequest(id=task_id))
        return _task_result(task)


async def cancel_task(entry: AgentEntry, card: AgentCard, task_id: str) -> A2AResult:
    async with _http_client(entry) as http_client:
        client = _build_client(http_client, card)
        task = await client.cancel_task(CancelTaskRequest(id=task_id))
        return _task_result(task)
