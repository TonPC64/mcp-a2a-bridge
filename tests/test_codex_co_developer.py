import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import asyncio
import httpx
from a2a.types import Message, Part, Role, Task

from examples import run_codex_co_developer as codex_module


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_codex_bin_resolves_even_with_a_minimal_path():
    """Reproduces the launchd bug: a minimal PATH must still find codex.

    codex-co-developer is often started by launchd (see
    scripts/com.codex-co-developer.plist), whose PATH is a bare
    "/usr/bin:/bin:/usr/sbin:/sbin" that excludes Homebrew's bin directory
    where `codex` actually lives. Without a fixed-directory fallback,
    shutil.which("codex") returns None under that PATH and the subprocess
    call fails with "No such file or directory: 'codex'".
    """
    repo_root = Path(__file__).resolve().parent.parent
    script = (
        "import sys; sys.path.insert(0, %r); "
        "from examples.run_codex_co_developer import _resolve_codex_bin; "
        "print(_resolve_codex_bin())" % str(repo_root)
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())},
        capture_output=True,
        text=True,
        timeout=15,
    )

    resolved = result.stdout.strip()
    assert result.returncode == 0, result.stderr
    assert resolved, "expected a non-empty resolved codex path"
    assert Path(resolved).is_absolute(), f"expected an absolute path, got {resolved!r}"


def test_codex_executor_uses_the_resolved_codex_binary():
    source = Path("examples/run_codex_co_developer.py").read_text()

    assert '"codex"\n                    if' not in source
    assert "CODEX_BIN," in source
    assert '"/bin/zsh",' in source
    assert '"-dfc",' in source
    assert 'source "$HOME/.zshrc" >/dev/null 2>&1; exec' in source


async def test_codex_executor_sends_heartbeats_until_completion(monkeypatch):
    """Replacing async heartbeats with a blocking subprocess hides task progress."""
    release = asyncio.Event()
    heartbeat = asyncio.Event()
    calls = []

    class FakeProcess:
        returncode = None

        async def communicate(self):
            await release.wait()
            self.returncode = 0
            return b"done", b""

        async def wait(self):
            self.returncode = -15

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    class FakeUpdater:
        def __init__(self, *args):
            pass

        def new_agent_message(self, parts):
            return parts

        async def start_work(self, message=None):
            calls.append(("working", message))
            if message:
                heartbeat.set()

        async def complete(self, message):
            calls.append(("completed", message))

        async def failed(self, message):
            calls.append(("failed", message))

    async def create_process(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(codex_module, "TaskUpdater", FakeUpdater)
    monkeypatch.setattr(codex_module.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(codex_module, "HEARTBEAT_SECONDS", 0.01)
    executor = codex_module.CodexExecutor()
    context = SimpleNamespace(
        current_task=Task(id="t1", context_id="c1"),
        message=Message(role=Role.ROLE_USER, parts=[Part(text="hi")]),
    )

    run = asyncio.create_task(executor.execute(context, SimpleNamespace()))
    await heartbeat.wait()
    release.set()
    await run

    assert [state for state, _ in calls] == ["working", "working", "completed"]


def test_codex_co_developer_publishes_an_a2a_agent_card():
    port = _free_port()
    process = subprocess.Popen(
        ["uv", "run", "python", "examples/run_codex_co_developer.py", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/.well-known/agent-card.json"

    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                response = httpx.get(url, timeout=1)
                if response.status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            raise AssertionError("codex-co-developer did not publish an agent card")

        card = response.json()
        assert card["name"] == "codex-co-developer"
        assert card["supportedInterfaces"][0]["url"] == f"http://127.0.0.1:{port}/"
    finally:
        process.terminate()
        process.wait(timeout=10)
