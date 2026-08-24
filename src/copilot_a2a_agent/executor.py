"""Translate A2A requests into Copilot CLI runs."""

from __future__ import annotations

import logging

from a2a.helpers import get_message_text, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState

from mcp_a2a_bridge.activity_writer import build_activity_writer
from copilot_a2a_agent.runner import CopilotResult, RunnerError, event_progress, run_copilot, session_uuid, split_cwd

log = logging.getLogger(__name__)


def format_reply(result: CopilotResult) -> str:
    text = result.text.strip() or "(Copilot produced no message.)"
    return f"{text}\n\nFiles modified:\n" + "\n".join(f"- {path}" for path in result.files_modified) if result.files_modified else text


class CopilotExecutor(AgentExecutor):
    def __init__(self, default_cwd: str, timeout_s: float = 1800) -> None:
        self._default_cwd, self._timeout_s = default_cwd, timeout_s
        self._activity = build_activity_writer("copilot")

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
        prompt, cwd = split_cwd(get_message_text(context.message), self._default_cwd)
        if not prompt.strip():
            await updater.failed(updater.new_agent_message([Part(text="No instruction was provided.")]))
            self._record(task.id, source=source, state="failed", text="No instruction was provided.")
            return
        result = CopilotResult(session_id=session_uuid(task.context_id))
        last_note = ""
        try:
            async for event, result in run_copilot(prompt, cwd, result.session_id, timeout_s=self._timeout_s):
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
