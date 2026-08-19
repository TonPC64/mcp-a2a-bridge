import socket
import subprocess
import time

import httpx


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
