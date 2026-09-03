"""Opt-in local A2A adapter for the Codex 0.150.1 app-server protocol.

This starts a fresh app-server session over stdio.  ``--thread-id`` resumes a
persisted thread only when app-server reports that it accepts direct input;
it does not attach to an arbitrary terminal TUI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from a2a.helpers import get_message_text, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from fastapi import FastAPI

from mcp_a2a_bridge.config import load_registry, resolve_config_path


class StdioTransport:
    def __init__(self, codex_bin: str = "codex") -> None:
        self.codex_bin = codex_bin
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.codex_bin,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            await self.start()
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("Codex app-server is not running")
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError("Codex app-server closed its stdio stream")
        return json.loads(line)

    async def close(self) -> None:
        if self.process is None or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), 5)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


class CodexAppServerClient:
    def __init__(self, transport: Any, cwd: str | None = None) -> None:
        self.transport = transport
        self.cwd = cwd or os.getcwd()
        self._next_id = 0
        self._initialized = False
        self._thread_id: str | None = None
        self._lock = asyncio.Lock()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        await self.transport.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = await self.transport.receive()
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"Codex {method} failed: {message['error']}")
                return message.get("result", {})
            if "id" in message and "method" in message:
                # No approvals are expected with approvalPolicy=never. Deny any
                # unexpected server request instead of leaving the A2A call hung.
                await self.transport.send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "adapter does not handle server requests"}})

    async def _initialize(self) -> None:
        if not self._initialized:
            await self._request("initialize", {"clientInfo": {"name": "mcp-a2a-bridge", "version": "0.1.0"}, "capabilities": {"experimentalApi": False}})
            self._initialized = True

    async def complete(self, prompt: str, thread_id: str | None = None) -> str:
        async with self._lock:
            await self._initialize()
            if self._thread_id is None:
                if thread_id:
                    result = await self._request("thread/resume", {"threadId": thread_id})
                    thread = result.get("thread", {})
                    if thread.get("canAcceptDirectInput") is not True:
                        raise ValueError("known thread cannot accept direct input")
                    self._thread_id = thread_id
                else:
                    result = await self._request("thread/start", {"cwd": self.cwd, "sandbox": "workspace-write", "approvalPolicy": "never", "ephemeral": True})
                    self._thread_id = result["thread"]["id"]
            result = await self._request("turn/start", {"threadId": self._thread_id, "input": [{"type": "text", "text": prompt, "text_elements": []}]})
            turn_id = result["turn"]["id"]
            text = ""
            while True:
                message = await self.transport.receive()
                if message.get("method") == "item/agentMessage/delta":
                    params = message.get("params", {})
                    if params.get("threadId") == self._thread_id and params.get("turnId") == turn_id:
                        text += params.get("delta", "")
                elif message.get("method") == "turn/completed":
                    params = message.get("params", {})
                    turn = params.get("turn", {})
                    if params.get("threadId") == self._thread_id and turn.get("id") == turn_id:
                        if turn.get("status") != "completed":
                            raise RuntimeError(f"Codex turn ended with status {turn.get('status')}")
                        return text


class CodexExecutor(AgentExecutor):
    def __init__(self, client: CodexAppServerClient, thread_id: str | None = None) -> None:
        self.client, self.thread_id = client, thread_id

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        try:
            reply = await self.client.complete(get_message_text(context.message), self.thread_id)
            await updater.complete(updater.new_agent_message([Part(text=reply or "Codex returned no text.")]))
        except Exception as exc:
            await updater.failed(updater.new_agent_message([Part(text=f"Codex app-server failed: {exc}")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.current_task.context_id).cancel()


def build_card(port: int, name: str) -> AgentCard:
    return AgentCard(name=name, description="Codex CLI interactive app-server over local A2A", version="1.0.0", supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=f"http://127.0.0.1:{port}/")], default_input_modes=["text/plain"], default_output_modes=["text/plain"], capabilities=AgentCapabilities(streaming=False), skills=[AgentSkill(id="coding", name="Coding", description="Work with Codex in the current repository", tags=["code", "development"])])


def build_app(card: AgentCard, client: CodexAppServerClient, thread_id: str | None = None) -> FastAPI:
    handler = DefaultRequestHandler(agent_executor=CodexExecutor(client, thread_id), task_store=InMemoryTaskStore(), agent_card=card)
    app = FastAPI()
    add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card), jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"))
    return app


def remove_temporary_agent(path: Path, name: str, url: str) -> None:
    if not path.is_file():
        return
    raw = json.loads(path.read_text())
    agents = raw.get("agents") if isinstance(raw, dict) else None
    if isinstance(agents, dict) and agents.get(name, {}).get("url") == url and agents.get(name, {}).get("headers", {}) == {}:
        del agents[name]
        path.write_text(json.dumps(raw, indent=2) + "\n")


def register_temporary_agent(path: Path, name: str, url: str) -> None:
    raw = json.loads(path.read_text()) if path.is_file() else {"agents": {}}
    raw.setdefault("agents", {})[name] = {"url": url, "headers": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9013)
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--thread-id")
    args = parser.parse_args()
    if not 0 < args.port < 65536:
        parser.error("--port must be a valid TCP port")
    name = f"codex-interactive-{uuid.uuid4().hex[:12]}"
    url = f"http://127.0.0.1:{args.port}"
    registry_path = resolve_config_path()
    register_temporary_agent(registry_path, name, url)
    client = CodexAppServerClient(StdioTransport(args.codex_bin))
    try:
        uvicorn.run(build_app(build_card(args.port, name), client, args.thread_id), host="127.0.0.1", port=args.port, log_level="info")
    finally:
        remove_temporary_agent(registry_path, name, url)
        asyncio.run(client.transport.close())


if __name__ == "__main__":
    main()
