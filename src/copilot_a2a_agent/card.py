"""The Agent Card advertising Copilot CLI over A2A."""

from __future__ import annotations

import socket

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


def build_card(port: int, host: str = "127.0.0.1") -> AgentCard:
    return AgentCard(
        name=f"copilot-{socket.gethostname()}",
        description="GitHub Copilot CLI — a coding agent that can read and edit files and run commands.",
        version="0.1.0",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=f"http://{host}:{port}/")],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[AgentSkill(
            id="delegate-coding-task", name="Delegate a coding task",
            description="Ask Copilot to work in a local repository. Prefix with 'cwd: /path/to/repo' to choose it. Prefix with 'provider: litellm-auto' or 'provider: github' to select the model provider.",
            tags=["coding", "refactor", "tests", "shell", "repository"],
            examples=["cwd: /Users/me/project\nAdd a --verbose flag to the CLI and test it", "cwd: /Users/me/project\nWhy is test_auth failing?"],
        )],
    )
