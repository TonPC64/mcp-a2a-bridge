from dataclasses import dataclass

import pytest
from a2a.helpers import new_text_message
from a2a.types import Message, Role, Task, TaskState

from copilot_a2a_agent import executor as executor_mod
from copilot_a2a_agent.executor import CopilotExecutor
from copilot_a2a_agent.runner import CopilotResult, RunnerError


class RecordingQueue:
    def __init__(self): self.events = []
    async def enqueue_event(self, event): self.events.append(event)


@dataclass
class FakeContext:
    message: Message
    current_task: Task | None = None


def make_context(text): return FakeContext(new_text_message(text, role=Role.ROLE_USER))


def statuses(queue):
    return [TaskState.Name(state) for event in queue.events if (state := getattr(getattr(event, "status", None), "state", None)) is not None]


def texts(queue):
    return "\n".join(part.text for event in queue.events if (message := getattr(getattr(event, "status", None), "message", None)) for part in message.parts if part.text)


def stub_run(events, result):
    async def run(prompt, cwd, session, timeout_s, provider=None):
        for event in events: yield event, result
        yield {"type": "result"}, result
    return run


def stub_raise(exc):
    async def run(prompt, cwd, session, timeout_s):
        raise exc
        yield
    return run


@pytest.mark.anyio
async def test_task_is_enqueued_before_any_status_update(monkeypatch):
    monkeypatch.setattr(executor_mod, "run_copilot", stub_run([], CopilotResult(text="ok", exit_code=0)))
    queue = RecordingQueue(); await CopilotExecutor("/tmp").execute(make_context("do it"), queue)
    assert isinstance(queue.events[0], Task)


@pytest.mark.anyio
async def test_successful_run_completes_with_copilot_text(monkeypatch):
    monkeypatch.setattr(executor_mod, "run_copilot", stub_run([], CopilotResult(text="all done", exit_code=0)))
    queue = RecordingQueue(); await CopilotExecutor("/tmp").execute(make_context("do it"), queue)
    assert statuses(queue)[-1] == "TASK_STATE_COMPLETED" and "all done" in texts(queue)


@pytest.mark.anyio
async def test_progress_updates_use_the_working_state(monkeypatch):
    monkeypatch.setattr(executor_mod, "run_copilot", stub_run([{"type": "tool.execution_start", "data": {"toolName": "bash"}}], CopilotResult(text="ok", exit_code=0)))
    queue = RecordingQueue(); await CopilotExecutor("/tmp").execute(make_context("do it"), queue)
    assert "TASK_STATE_WORKING" in statuses(queue) and statuses(queue)[-1] == "TASK_STATE_COMPLETED"


@pytest.mark.anyio
@pytest.mark.parametrize("result, error", [(CopilotResult(text="boom", exit_code=1), None), (None, RunnerError("copilot not found")), (None, TimeoutError()), (None, ValueError("surprise"))])
async def test_failed_runs_become_failed_tasks(monkeypatch, result, error):
    monkeypatch.setattr(executor_mod, "run_copilot", stub_run([], result) if error is None else stub_raise(error))
    queue = RecordingQueue(); await CopilotExecutor("/tmp", timeout_s=5).execute(make_context("do it"), queue)
    assert statuses(queue)[-1] == "TASK_STATE_FAILED"


@pytest.mark.anyio
async def test_no_result_event_is_treated_as_failure(monkeypatch):
    async def silent(prompt, cwd, session, timeout_s):
        return
        yield
    monkeypatch.setattr(executor_mod, "run_copilot", silent)
    queue = RecordingQueue(); await CopilotExecutor("/tmp").execute(make_context("do it"), queue)
    assert statuses(queue)[-1] == "TASK_STATE_FAILED"


@pytest.mark.anyio
async def test_empty_instruction_fails_without_running_copilot(monkeypatch):
    monkeypatch.setattr(executor_mod, "run_copilot", lambda *args: (_ for _ in ()).throw(AssertionError("must not run")))
    queue = RecordingQueue(); await CopilotExecutor("/tmp").execute(make_context("cwd: /tmp/x\n"), queue)
    assert statuses(queue)[-1] == "TASK_STATE_FAILED"


@pytest.mark.anyio
async def test_provider_envelope_preserves_cwd_and_forwards_provider(monkeypatch):
    received = {}

    async def run(prompt, cwd, session, timeout_s, provider=None):
        received.update(prompt=prompt, cwd=cwd, provider=provider)
        yield {"type": "result"}, CopilotResult(text="ok", exit_code=0)

    monkeypatch.setattr(executor_mod, "run_copilot", run)
    queue = RecordingQueue()
    await CopilotExecutor("/default").execute(
        make_context("provider: litellm-auto\ncwd: /tmp/project\ninspect it"), queue
    )
    assert received == {"prompt": "inspect it", "cwd": "/tmp/project", "provider": "litellm-auto"}
