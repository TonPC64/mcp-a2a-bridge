"""Load and persist the shared A2A agent registry. No network, no A2A imports."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "a2a-bridge" / "agents.json"
LOCAL_CONFIG_NAME = ".a2a-agents.json"
ENV_VAR = "A2A_BRIDGE_CONFIG"


class ConfigError(Exception):
    """The registry file exists but cannot be used."""


@dataclass(frozen=True)
class AgentEntry:
    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Registry:
    path: Path | None
    agents: dict[str, AgentEntry]


def resolve_config_path(
    env: Mapping[str, str] | None = None, cwd: Path | None = None
) -> Path:
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd

    override = env.get(ENV_VAR)
    if override:
        return Path(override).expanduser()

    local = cwd / LOCAL_CONFIG_NAME
    if local.is_file():
        return local

    return DEFAULT_CONFIG_PATH


def load_registry(path: Path) -> Registry:
    if not path.is_file():
        return Registry(path=path, agents={})

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f'{path} must be a JSON object with an "agents" key')

    specs = raw.get("agents", {})
    if not isinstance(specs, dict):
        raise ConfigError(f'{path}: "agents" must be an object')

    agents: dict[str, AgentEntry] = {}
    for name, spec in specs.items():
        if not isinstance(spec, dict):
            raise ConfigError(f'{path}: agent "{name}" must be an object')

        url = spec.get("url")
        if not isinstance(url, str) or not url:
            raise ConfigError(f'{path}: agent "{name}" is missing a "url" string')

        headers = spec.get("headers", {})
        if not isinstance(headers, dict):
            raise ConfigError(f'{path}: agent "{name}" has a non-object "headers"')

        agents[name] = AgentEntry(
            name=name,
            url=url.rstrip("/"),
            headers={str(k): str(v) for k, v in headers.items()},
        )

    return Registry(path=path, agents=agents)


def save_agent(path: Path, entry: AgentEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    raw: dict = {"agents": {}}
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
        if isinstance(existing, dict):
            raw = existing
            if not isinstance(raw.get("agents"), dict):
                raw["agents"] = {}

    raw["agents"][entry.name] = {"url": entry.url, "headers": entry.headers}
    path.write_text(json.dumps(raw, indent=2) + "\n")
