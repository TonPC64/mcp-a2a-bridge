import asyncio

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

from mcp_a2a_bridge.client import (
    A2AResult,
    BLOCKING_STATES,
    TERMINAL_STATES,
    card_summary,
    consume_stream,
    is_done,
    state_name,
    summarize_part,
)


def test_state_name_strips_prefix_and_lowercases():
    assert state_name(TaskState.TASK_STATE_COMPLETED) == "completed"
    assert state_name(TaskState.TASK_STATE_INPUT_REQUIRED) == "input_required"
    assert state_name(TaskState.TASK_STATE_WORKING) == "working"


def test_terminal_and_blocking_state_membership():
    assert TaskState.TASK_STATE_COMPLETED in TERMINAL_STATES
    assert TaskState.TASK_STATE_FAILED in TERMINAL_STATES
    assert TaskState.TASK_STATE_CANCELED in TERMINAL_STATES
    assert TaskState.TASK_STATE_REJECTED in TERMINAL_STATES
    assert TaskState.TASK_STATE_INPUT_REQUIRED in BLOCKING_STATES
    assert TaskState.TASK_STATE_AUTH_REQUIRED in BLOCKING_STATES
    assert TaskState.TASK_STATE_WORKING not in TERMINAL_STATES
    assert TaskState.TASK_STATE_WORKING not in BLOCKING_STATES


def test_is_done_covers_terminal_and_blocking():
    assert is_done(TaskState.TASK_STATE_COMPLETED) is True
    assert is_done(TaskState.TASK_STATE_INPUT_REQUIRED) is True
    assert is_done(TaskState.TASK_STATE_WORKING) is False


def test_summarize_text_part_returns_text():
    assert summarize_part(Part(text="hello")) == "hello"


def test_summarize_raw_part_never_leaks_bytes():
    part = Part(raw=b"\x89PNG\r\n", media_type="image/png", filename="pic.png")
    assert summarize_part(part) == "[file: pic.png, image/png, 6 bytes]"


def test_summarize_url_part():
    part = Part(url="https://example.com/f.pdf", media_type="application/pdf")
    assert summarize_part(part) == "[file: https://example.com/f.pdf, application/pdf]"


def test_summarize_data_part_inlines_json():
    part = Part(data=ParseDict({"key": "value"}, Value()))
    assert summarize_part(part) == '{"key": "value"}'


def test_summarize_part_returns_empty_string_for_unset_content():
    assert summarize_part(Part()) == ""


def test_card_summary_flattens_useful_fields():
    card = AgentCard(
        name="Planner",
        description="Plans things",
        version="1.2.3",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="http://localhost:9001/",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="plan",
                name="Plan",
                description="Make a plan",
                tags=["planning"],
                examples=["plan a trip"],
            )
        ],
    )
    summary = card_summary(card)

    assert summary["name"] == "Planner"
    assert summary["version"] == "1.2.3"
    assert summary["url"] == "http://localhost:9001/"
    assert summary["streaming"] is True
    assert summary["skills"] == [
        {
            "id": "plan",
            "name": "Plan",
            "description": "Make a plan",
            "tags": ["planning"],
            "examples": ["plan a trip"],
        }
    ]


def test_card_summary_handles_card_with_no_interfaces():
    summary = card_summary(AgentCard(name="Bare", description="d", version="1"))
    assert summary["url"] is None
    assert summary["skills"] == []
    assert summary["streaming"] is False


def test_result_to_dict_omits_empty_ids():
    result = A2AResult(
        state="completed", text="hi", task_id=None, context_id=None, done=True
    )
    assert result.to_dict() == {"state": "completed", "text": "hi", "done": True}


def test_result_to_dict_includes_ids_when_present():
    result = A2AResult(
        state="working", text="", task_id="t1", context_id="c1", done=False
    )
    assert result.to_dict() == {
        "state": "working",
        "text": "",
        "done": False,
        "task_id": "t1",
        "context_id": "c1",
    }


# Tests for consume_stream


async def as_stream(items):
    for item in items:
        yield item


def task_chunk(task_id, state):
    return StreamResponse(
        task=Task(id=task_id, context_id="c1", status=TaskStatus(state=state))
    )


def status_chunk(task_id, state, text=None):
    message = None
    if text is not None:
        message = Message(role=Role.ROLE_AGENT, parts=[Part(text=text)])
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id=task_id,
            context_id="c1",
            status=TaskStatus(state=state, message=message),
        )
    )


async def test_consume_stream_accumulates_text_until_completed():
    chunks = as_stream([
        task_chunk("t1", TaskState.TASK_STATE_SUBMITTED),
        status_chunk("t1", TaskState.TASK_STATE_WORKING, "thinking"),
        status_chunk("t1", TaskState.TASK_STATE_COMPLETED, "done"),
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.state == "completed"
    assert result.text == "thinking\ndone"
    assert result.task_id == "t1"
    assert result.context_id == "c1"
    assert result.done is True


async def test_consume_stream_stops_at_input_required():
    chunks = as_stream([
        task_chunk("t1", TaskState.TASK_STATE_SUBMITTED),
        status_chunk("t1", TaskState.TASK_STATE_INPUT_REQUIRED, "which city?"),
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.state == "input_required"
    assert result.text == "which city?"
    assert result.task_id == "t1"
    assert result.done is True


async def test_consume_stream_reports_failed_as_data_not_exception():
    chunks = as_stream([
        task_chunk("t1", TaskState.TASK_STATE_SUBMITTED),
        status_chunk("t1", TaskState.TASK_STATE_FAILED, "upstream exploded"),
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.state == "failed"
    assert "upstream exploded" in result.text
    assert result.done is True


async def test_consume_stream_handles_direct_message_reply():
    chunks = as_stream([
        StreamResponse(message=Message(role=Role.ROLE_AGENT, parts=[Part(text="hi")]))
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.text == "hi"
    assert result.state == "completed"
    assert result.done is True


async def test_consume_stream_timeout_returns_working_with_task_id():
    async def slow():
        yield task_chunk("t1", TaskState.TASK_STATE_SUBMITTED)
        await asyncio.sleep(5)
        yield status_chunk("t1", TaskState.TASK_STATE_COMPLETED, "never seen")

    result = await consume_stream(slow(), task_id=None, context_id=None, timeout_s=0.1)

    assert result.state == "working"
    assert result.task_id == "t1"
    assert result.done is False
    assert "never seen" not in result.text


async def test_consume_stream_closes_iterator_on_timeout():
    closed = False

    async def slow():
        nonlocal closed
        try:
            yield task_chunk("t1", TaskState.TASK_STATE_SUBMITTED)
            await asyncio.sleep(5)
            yield status_chunk("t1", TaskState.TASK_STATE_COMPLETED, "never seen")
        finally:
            closed = True

    result = await consume_stream(slow(), task_id=None, context_id=None, timeout_s=0.1)

    assert result.state == "working"
    assert result.task_id == "t1"
    assert closed is True


async def test_consume_stream_preserves_incoming_task_id_when_stream_is_silent():
    chunks = as_stream([
        StreamResponse(message=Message(role=Role.ROLE_AGENT, parts=[Part(text="ok")]))
    ])
    result = await consume_stream(chunks, task_id="given", context_id="ctx")

    assert result.task_id == "given"
    assert result.context_id == "ctx"
