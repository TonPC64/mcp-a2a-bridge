import json
import os

import pytest

from examples.run_codex_interactive_a2a import CodexAppServerClient, StdioTransport, remove_temporary_agent


class FakeTransport:
    def __init__(self):
        self.requests = []
        self.incoming = []

    async def send(self, message):
        self.requests.append(message)
        method = message["method"]
        request_id = message["id"]
        if method == "initialize":
            self.incoming.append({"id": request_id, "result": {"userAgent": "fake"}})
        elif method == "thread/start":
            self.incoming.append({"id": request_id, "result": {"thread": {"id": "thread-1"}}})
        elif method == "turn/start":
            self.incoming.extend(
                [
                    {"id": request_id, "result": {"turn": {"id": "turn-1"}}},
                    {
                        "method": "item/agentMessage/delta",
                        "params": {"delta": "hello ", "threadId": "thread-1", "turnId": "turn-1"},
                    },
                    {
                        "method": "item/agentMessage/delta",
                        "params": {"delta": "world", "threadId": "thread-1", "turnId": "turn-1"},
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turn": {"id": "turn-1", "status": "completed"},
                        },
                    },
                ]
            )

    async def receive(self):
        return self.incoming.pop(0)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_client_maps_a_turn_and_streamed_completion():
    transport = FakeTransport()
    client = CodexAppServerClient(transport)

    assert await client.complete("say hello") == "hello world"
    assert [request["method"] for request in transport.requests] == [
        "initialize",
        "thread/start",
        "turn/start",
    ]
    assert transport.requests[-1]["params"]["input"] == [
        {"type": "text", "text": "say hello", "text_elements": []}
    ]


@pytest.mark.asyncio
async def test_client_rejects_resume_of_thread_that_cannot_accept_input():
    class ResumeTransport(FakeTransport):
        async def send(self, message):
            await super().send(message)
            if message["method"] == "thread/resume":
                self.incoming.append(
                    {
                        "id": message["id"],
                        "result": {"thread": {"id": "old", "canAcceptDirectInput": False}},
                    }
                )

    client = CodexAppServerClient(ResumeTransport())
    with pytest.raises(ValueError, match="cannot accept direct input"):
        await client.complete("hello", thread_id="old")


def test_remove_temporary_agent_only_removes_exact_entry(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "codex-interactive-x": {"url": "http://127.0.0.1:9013", "headers": {}},
                    "other": {"url": "http://127.0.0.1:9013", "headers": {}},
                }
            }
        )
    )

    remove_temporary_agent(path, "codex-interactive-x", "http://127.0.0.1:9013")

    remaining = json.loads(path.read_text())["agents"]
    assert "codex-interactive-x" not in remaining
    assert "other" in remaining


@pytest.mark.asyncio
async def test_live_app_server_smoke_starts_a_safe_ephemeral_thread():
    if os.environ.get("CODEX_APP_SERVER_LIVE") != "1":
        pytest.skip("set CODEX_APP_SERVER_LIVE=1 to run the local Codex smoke test")
    transport = StdioTransport()
    client = CodexAppServerClient(transport)
    try:
        await client._initialize()
        result = await client._request(
            "thread/start",
            {"cwd": os.getcwd(), "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True},
        )
        assert result["thread"]["id"]
    finally:
        await transport.close()
