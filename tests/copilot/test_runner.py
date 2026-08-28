import pytest

from run_copilot_main_dev import CopilotResult, apply_event, build_argv, event_progress, parse_line, session_uuid, split_cwd

ASSISTANT_MESSAGE = '{"type":"assistant.message","data":{"messageId":"21762f90","model":"claude-opus-5","content":"pong","toolRequests":[],"turnId":"0"},"id":"41a4e0cd"}'
RESULT = '{"type":"result","sessionId":"1e23377d-fcd1-43a0-814c-d96c298f8d41","exitCode":0,"usage":{"codeChanges":{"filesModified":["src/app.py"]}}}'


def test_parse_line_reads_a_real_event():
    assert parse_line(ASSISTANT_MESSAGE)["data"]["content"] == "pong"


@pytest.mark.parametrize("line", ["", "   ", "Welcome to Copilot", "not json {"])
def test_parse_line_ignores_non_json_noise(line):
    assert parse_line(line) is None


def test_apply_event_captures_assistant_text():
    assert apply_event(parse_line(ASSISTANT_MESSAGE), CopilotResult()).text == "pong"


def test_apply_event_captures_result_metadata():
    result = apply_event(parse_line(RESULT), CopilotResult())
    assert (result.session_id, result.exit_code, result.files_modified, result.ok) == ("1e23377d-fcd1-43a0-814c-d96c298f8d41", 0, ["src/app.py"], True)


def test_nonzero_exit_is_not_ok():
    assert not CopilotResult(exit_code=1).ok


def test_later_assistant_message_wins():
    result = apply_event(parse_line(ASSISTANT_MESSAGE), CopilotResult())
    assert apply_event({"type": "assistant.message", "data": {"content": "final answer"}}, result).text == "final answer"


def test_empty_assistant_message_does_not_erase_text():
    assert apply_event({"type": "assistant.message", "data": {"content": ""}}, CopilotResult(text="kept")).text == "kept"


def test_split_cwd_extracts_leading_directory():
    assert split_cwd("cwd: /tmp/proj\nfix the bug", "/default") == ("fix the bug", "/tmp/proj")


def test_split_cwd_falls_back_to_default():
    assert split_cwd("fix the bug", "/default") == ("fix the bug", "/default")


def test_split_cwd_ignores_empty_path():
    assert split_cwd("cwd:\nfix it", "/default")[1] == "/default"


def test_build_argv_is_non_interactive_and_scoped():
    argv = build_argv("do it", "/repo", "abc-123")
    assert argv[:3] == ["copilot", "-p", "do it"] and "--allow-all-tools" in argv and argv[argv.index("-C") + 1] == "/repo"


def test_session_uuid_passes_through_a_real_uuid():
    assert session_uuid("1e23377d-fcd1-43a0-814c-d96c298f8d41") == "1e23377d-fcd1-43a0-814c-d96c298f8d41"


def test_session_uuid_derives_stable_uuid_for_non_uuid_context():
    assert session_uuid("ctx-4c636f9f") == session_uuid("ctx-4c636f9f") != session_uuid("ctx-other")


def test_event_progress_reports_tool_use_and_ignores_noise():
    assert event_progress({"type": "tool.execution_start", "data": {"toolName": "bash"}}) == "running bash"
    assert event_progress({"type": "session.skills_loaded", "data": {}}) is None


class FakeStdout:
    def __init__(self, chunks): self.chunks = iter(chunks)
    async def read(self, size=-1): return next(self.chunks, b"")


class FakeProcess:
    def __init__(self, output, stderr=b""):
        self.stdout, self.stderr, self.returncode = FakeStdout([output]), FakeStdout([stderr]), 0
    def kill(self): self.returncode = -9
    async def wait(self): return self.returncode


@pytest.mark.anyio
async def test_run_copilot_accepts_json_lines_larger_than_stream_limit(monkeypatch):
    import run_copilot_main_dev as runner
    process = FakeProcess(("{\"type\":\"assistant.message\",\"data\":{\"content\":\"" + "x" * 100_000 + "\"}}\n{\"type\":\"result\",\"exitCode\":0}\n").encode())
    async def create_process(*args, **kwargs): return process
    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_process)
    assert [event["type"] async for event, _ in runner.run_copilot("review", "/tmp", "session")] == ["assistant.message", "result"]


@pytest.mark.anyio
async def test_run_copilot_drains_stderr_while_reading_events(monkeypatch):
    import run_copilot_main_dev as runner

    process = FakeProcess(b'{"type":"result","exitCode":0}\n', b"Copilot diagnostic\n")

    async def create_process(*args, **kwargs): return process

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", create_process)
    assert [event["type"] async for event, _ in runner.run_copilot("review", "/tmp", "session")] == ["result"]
    assert next(process.stderr.chunks, None) is None
