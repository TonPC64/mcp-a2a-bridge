"""A2A client calls and protobuf-to-plain-data normalization."""

from __future__ import annotations

from dataclasses import dataclass

from a2a.types import AgentCard, Part, TaskState
from google.protobuf.json_format import MessageToJson

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
