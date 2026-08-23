from pathlib import Path

from examples.run_claude_reviewer import _process_reply


def test_reviewer_does_not_start_zsh():
    source = Path("examples/run_claude_reviewer.py").read_text()

    assert "asyncio.create_subprocess_exec(\n                *command," in source
    assert 'shell_command = ["/bin/zsh"' not in source


def test_process_reply_includes_stderr_for_failed_review():
    assert _process_reply(b"", b"authentication failed", 1) == (
        "Claude exited with status 1: authentication failed"
    )


def test_process_reply_does_not_return_empty_success():
    assert _process_reply(b"", b"", 0) == "Claude returned no review text."
