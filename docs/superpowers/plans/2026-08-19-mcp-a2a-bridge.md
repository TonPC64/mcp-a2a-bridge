# mcp-a2a-bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stdio MCP server that lets Copilot CLI, Claude Code, Codex, and Hermes call remote A2A agents as an A2A client.

**Architecture:** Four modules with one responsibility each. `config.py` loads a JSON agent registry (stdlib only). `registry.py` resolves and caches A2A agent cards. `client.py` wraps `a2a-sdk` and flattens protobuf responses into plain dicts. `server.py` declares five MCP tools and contains no A2A logic. MCP knowledge lives only in `server.py`; A2A knowledge lives only in `registry.py` and `client.py`.

**Tech Stack:** Python 3.11+, `a2a-sdk>=1.1,<2` (protobuf types), `mcp>=2.0,<3` (`MCPServer`), `httpx`, `pytest`, `pytest-asyncio`, `uv`/`uvx`.

**Spec:** `docs/superpowers/specs/2026-08-19-mcp-a2a-bridge-design.md`

## Global Constraints

- Repo root: `~/WorkSpace/mcp-a2a-bridge`. Package name `mcp-a2a-bridge`, import name `mcp_a2a_bridge`, source under `src/`.
- `requires-python = ">=3.11"`. `asyncio.timeout()` does not exist in 3.10.
- Dependencies exactly: `mcp>=2.0.0,<3`, `a2a-sdk>=1.1,<2`, `httpx>=0.28.1`. Dev extras: `pytest`, `pytest-asyncio`, `a2a-sdk[http-server]`, `fastapi`, `uvicorn`.
- **Use `from mcp.server.mcpserver import MCPServer`.** `mcp.server.fastmcp.FastMCP` was removed in mcp 2.0 and does not exist.
- **A2A types are protobuf**, imported from `a2a.types`. Enum values are `TaskState.TASK_STATE_COMPLETED` style. Read enum names with `TaskState.Name(value)`.
- **Protobuf oneof names, verified:** `Part` → `"content"` (`text`, `raw`, `url`, `data`). `StreamResponse` → `"payload"` (`task`, `message`, `status_update`, `artifact_update`).
- `GetTaskRequest` and `CancelTaskRequest` take **`id=`**, not `task_id=`.
- `Client.send_message()` returns `AsyncIterator[StreamResponse]` in both streaming and non-streaming modes.
- `create_client()` and `ClientFactory.create_from_url()` are **async**; `ClientFactory.create(card)` is **sync**.
- Terminal states: `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`. Blocking (not terminal): `INPUT_REQUIRED`, `AUTH_REQUIRED`.
- Agent card path is `/.well-known/agent-card.json` (the SDK default; never hardcode it).
- Never return base64 blobs to the host. Summarize non-text parts.
- Tests mock only the network boundary or inject a fake card fetcher. Never mock our own modules.
- Every task ends with a commit. Commit messages use Conventional Commits and end with:
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, console script, pytest config |
| `src/mcp_a2a_bridge/__init__.py` | Version constant only |
| `src/mcp_a2a_bridge/config.py` | Resolve/load/validate/write the registry file. No network, no A2A imports |
| `src/mcp_a2a_bridge/registry.py` | Fetch and cache `AgentCard`s; agent lookup errors |
| `src/mcp_a2a_bridge/client.py` | `a2a-sdk` calls; flatten protobuf to dicts; timeout handling |
| `src/mcp_a2a_bridge/server.py` | Five MCP tools + `main()` |
| `tests/test_config.py` | Config resolution and validation |
| `tests/test_registry.py` | Card caching, refresh, reachability |
| `tests/test_client.py` | Part summarization, state naming, stream accumulation |
| `tests/echo_agent.py` | Real in-process A2A echo agent |
| `tests/test_integration.py` | Real A2A round trip through the bridge |
| `tests/test_server.py` | Tool registration and error mapping |
| `README.md` | Install and per-host registration |

---

### Task 1: Project scaffolding and config module

**Files:**
- Create: `pyproject.toml`, `src/mcp_a2a_bridge/__init__.py`, `src/mcp_a2a_bridge/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ConfigError`, `AgentEntry(name: str, url: str, headers: dict[str,str])`, `Registry(path: Path | None, agents: dict[str, AgentEntry])`, `DEFAULT_CONFIG_PATH: Path`, `resolve_config_path(env: Mapping[str,str] | None = None, cwd: Path | None = None) -> Path`, `load_registry(path: Path) -> Registry`, `save_agent(path: Path, entry: AgentEntry) -> None`.

- [ ] **Step 1: Create the package skeleton**

```bash
cd ~/WorkSpace/mcp-a2a-bridge
mkdir -p src/mcp_a2a_bridge tests
touch tests/__init__.py
```

Create `pyproject.toml`:

```toml
[project]
name = "mcp-a2a-bridge"
version = "0.1.0"
description = "MCP server that lets coding agents call remote A2A agents"
requires-python = ">=3.11"
dependencies = [
    "mcp>=2.0.0,<3",
    "a2a-sdk>=1.1,<2",
    "httpx>=0.28.1",
]

[project.scripts]
mcp-a2a-bridge = "mcp_a2a_bridge.server:main"

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "a2a-sdk[http-server]>=1.1,<2",
    "fastapi>=0.115",
    "uvicorn>=0.30",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mcp_a2a_bridge"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Create `src/mcp_a2a_bridge/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 2: Install the environment**

```bash
cd ~/WorkSpace/mcp-a2a-bridge
uv venv --python 3.11
uv pip install -e ".[dev]"
```

Verify:

```bash
.venv/bin/python -c "import mcp_a2a_bridge; print(mcp_a2a_bridge.__version__)"
```

Expected output: `0.1.0`

- [ ] **Step 3: Write the failing config tests**

Create `tests/test_config.py`:

```python
import json

import pytest

from mcp_a2a_bridge.config import (
    AgentEntry,
    ConfigError,
    DEFAULT_CONFIG_PATH,
    load_registry,
    resolve_config_path,
    save_agent,
)


def test_env_override_wins(tmp_path):
    target = tmp_path / "custom.json"
    got = resolve_config_path(env={"A2A_BRIDGE_CONFIG": str(target)}, cwd=tmp_path)
    assert got == target


def test_project_local_file_used_when_present(tmp_path):
    local = tmp_path / ".a2a-agents.json"
    local.write_text('{"agents": {}}')
    got = resolve_config_path(env={}, cwd=tmp_path)
    assert got == local


def test_falls_back_to_default_path(tmp_path):
    got = resolve_config_path(env={}, cwd=tmp_path)
    assert got == DEFAULT_CONFIG_PATH


def test_missing_file_is_empty_registry_not_an_error(tmp_path):
    registry = load_registry(tmp_path / "nope.json")
    assert registry.agents == {}
    assert registry.path == tmp_path / "nope.json"


def test_loads_agents(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({
        "agents": {
            "planner": {"url": "http://localhost:9001/", "headers": {"X-Trace": "1"}},
            "bare": {"url": "http://localhost:9002"},
        }
    }))
    registry = load_registry(path)
    assert registry.agents["planner"] == AgentEntry(
        name="planner", url="http://localhost:9001", headers={"X-Trace": "1"}
    )
    assert registry.agents["bare"].headers == {}


def test_invalid_json_raises_config_error(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_registry(path)


def test_agent_without_url_names_the_offending_key(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": {"broken": {"headers": {}}}}))
    with pytest.raises(ConfigError, match="broken"):
        load_registry(path)


def test_save_agent_creates_file_and_parents(tmp_path):
    path = tmp_path / "nested" / "agents.json"
    save_agent(path, AgentEntry(name="planner", url="http://localhost:9001", headers={}))
    written = json.loads(path.read_text())
    assert written["agents"]["planner"]["url"] == "http://localhost:9001"


def test_save_agent_preserves_existing_entries(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": {"old": {"url": "http://localhost:1", "headers": {}}}}))
    save_agent(path, AgentEntry(name="new", url="http://localhost:2", headers={}))
    written = json.loads(path.read_text())
    assert set(written["agents"]) == {"old", "new"}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'mcp_a2a_bridge.config'`

- [ ] **Step 5: Implement config.py**

Create `src/mcp_a2a_bridge/config.py`:

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 9 passed

- [ ] **Step 7: Commit**

```bash
cd ~/WorkSpace/mcp-a2a-bridge
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
dist/
.pytest_cache/
EOF
git add pyproject.toml .gitignore src/mcp_a2a_bridge/__init__.py src/mcp_a2a_bridge/config.py tests/
git commit -m "feat: add agent registry config loading

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 2: Agent card registry

**Files:**
- Create: `src/mcp_a2a_bridge/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `AgentEntry`, `Registry`, `save_agent` from `mcp_a2a_bridge.config`.
- Produces: `UnknownAgentError`, `AgentUnreachableError`, `ResolvedAgent(entry, card, error)` with property `reachable: bool`, `fetch_card_over_http(entry) -> AgentCard`, and `AgentRegistry` with:
  - `__init__(self, registry: Registry, fetch_card: Callable[[AgentEntry], Awaitable[AgentCard]] | None = None)`
  - `names(self) -> list[str]`
  - `entry(self, name: str) -> AgentEntry`
  - `config_path` property → `Path | None`
  - `async card(self, name: str) -> AgentCard`
  - `async resolve_all(self, refresh: bool = False) -> list[ResolvedAgent]`
  - `async add(self, name: str, url: str, headers: dict[str,str] | None, persist: bool) -> ResolvedAgent`
  - `clear_cache(self) -> None`

The injectable `fetch_card` lets tests supply a fake without mocking our own modules; production uses the real default.

- [ ] **Step 1: Write the failing registry tests**

Create `tests/test_registry.py`:

```python
import pytest
from a2a.types import AgentCard

from mcp_a2a_bridge.config import AgentEntry, Registry, load_registry
from mcp_a2a_bridge.registry import (
    AgentRegistry,
    AgentUnreachableError,
    UnknownAgentError,
)


def make_card(name: str) -> AgentCard:
    return AgentCard(name=name, description="d", version="1.0.0")


def registry_with(**agents: str) -> Registry:
    return Registry(
        path=None,
        agents={n: AgentEntry(name=n, url=u, headers={}) for n, u in agents.items()},
    )


async def test_card_is_fetched_once_and_cached():
    calls = []

    async def fetch(entry):
        calls.append(entry.name)
        return make_card(entry.name)

    reg = AgentRegistry(registry_with(planner="http://x"), fetch_card=fetch)
    first = await reg.card("planner")
    second = await reg.card("planner")

    assert first.name == "planner"
    assert second is first
    assert calls == ["planner"]


async def test_refresh_refetches():
    calls = []

    async def fetch(entry):
        calls.append(entry.name)
        return make_card(entry.name)

    reg = AgentRegistry(registry_with(planner="http://x"), fetch_card=fetch)
    await reg.card("planner")
    await reg.resolve_all(refresh=True)
    assert calls == ["planner", "planner"]


def test_unknown_agent_lists_valid_names():
    reg = AgentRegistry(registry_with(planner="http://x", other="http://y"))
    with pytest.raises(UnknownAgentError) as exc:
        reg.entry("nope")
    assert "planner" in str(exc.value)
    assert "other" in str(exc.value)


async def test_unreachable_agent_raises_with_url_and_cause():
    async def fetch(entry):
        raise RuntimeError("connection refused")

    reg = AgentRegistry(registry_with(planner="http://localhost:9001"), fetch_card=fetch)
    with pytest.raises(AgentUnreachableError) as exc:
        await reg.card("planner")
    assert "http://localhost:9001" in str(exc.value)
    assert "connection refused" in str(exc.value)


async def test_resolve_all_reports_failures_without_raising():
    async def fetch(entry):
        if entry.name == "bad":
            raise RuntimeError("boom")
        return make_card(entry.name)

    reg = AgentRegistry(registry_with(good="http://x", bad="http://y"), fetch_card=fetch)
    resolved = {r.entry.name: r for r in await reg.resolve_all()}

    assert resolved["good"].reachable is True
    assert resolved["good"].error is None
    assert resolved["bad"].reachable is False
    assert "boom" in resolved["bad"].error


async def test_add_persists_only_after_successful_resolution(tmp_path):
    path = tmp_path / "agents.json"

    async def fetch(entry):
        return make_card(entry.name)

    reg = AgentRegistry(Registry(path=path, agents={}), fetch_card=fetch)
    await reg.add("planner", "http://localhost:9001/", None, persist=True)

    assert load_registry(path).agents["planner"].url == "http://localhost:9001"
    assert reg.names() == ["planner"]


async def test_add_does_not_persist_unreachable_agent(tmp_path):
    path = tmp_path / "agents.json"

    async def fetch(entry):
        raise RuntimeError("nope")

    reg = AgentRegistry(Registry(path=path, agents={}), fetch_card=fetch)
    with pytest.raises(AgentUnreachableError):
        await reg.add("planner", "http://localhost:9001", None, persist=True)

    assert not path.exists()
    assert reg.names() == []


async def test_add_without_persist_is_in_memory_only(tmp_path):
    path = tmp_path / "agents.json"

    async def fetch(entry):
        return make_card(entry.name)

    reg = AgentRegistry(Registry(path=path, agents={}), fetch_card=fetch)
    await reg.add("planner", "http://localhost:9001", None, persist=False)

    assert reg.names() == ["planner"]
    assert not path.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_registry.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'mcp_a2a_bridge.registry'`

- [ ] **Step 3: Implement registry.py**

Create `src/mcp_a2a_bridge/registry.py`:

```python
"""Resolve and cache A2A agent cards."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from a2a.client import A2ACardResolver
from a2a.types import AgentCard

from mcp_a2a_bridge.config import AgentEntry, Registry, save_agent

CARD_FETCH_TIMEOUT_S = 30.0


class UnknownAgentError(Exception):
    """No agent with that name is configured."""


class AgentUnreachableError(Exception):
    """The agent is configured but its card could not be fetched."""


@dataclass
class ResolvedAgent:
    entry: AgentEntry
    card: AgentCard | None
    error: str | None

    @property
    def reachable(self) -> bool:
        return self.card is not None


async def fetch_card_over_http(entry: AgentEntry) -> AgentCard:
    async with httpx.AsyncClient(
        headers=entry.headers, timeout=CARD_FETCH_TIMEOUT_S
    ) as http_client:
        resolver = A2ACardResolver(httpx_client=http_client, base_url=entry.url)
        return await resolver.get_agent_card()


class AgentRegistry:
    def __init__(
        self,
        registry: Registry,
        fetch_card: Callable[[AgentEntry], Awaitable[AgentCard]] | None = None,
    ) -> None:
        self._registry = registry
        self._fetch_card = fetch_card or fetch_card_over_http
        self._cards: dict[str, AgentCard] = {}

    @property
    def config_path(self) -> Path | None:
        return self._registry.path

    def names(self) -> list[str]:
        return list(self._registry.agents)

    def entry(self, name: str) -> AgentEntry:
        try:
            return self._registry.agents[name]
        except KeyError:
            known = ", ".join(sorted(self._registry.agents)) or "(none configured)"
            raise UnknownAgentError(
                f'Unknown agent "{name}". Configured agents: {known}'
            ) from None

    async def card(self, name: str) -> AgentCard:
        entry = self.entry(name)
        cached = self._cards.get(name)
        if cached is not None:
            return cached

        card = await self._fetch_one(entry)
        self._cards[name] = card
        return card

    async def _fetch_one(self, entry: AgentEntry) -> AgentCard:
        try:
            return await self._fetch_card(entry)
        except Exception as exc:
            raise AgentUnreachableError(
                f'Could not fetch the agent card for "{entry.name}" '
                f"at {entry.url}: {exc}"
            ) from exc

    async def resolve_all(self, refresh: bool = False) -> list[ResolvedAgent]:
        if refresh:
            self.clear_cache()

        entries = list(self._registry.agents.values())
        results = await asyncio.gather(*(self._resolve_one(e) for e in entries))
        return list(results)

    async def _resolve_one(self, entry: AgentEntry) -> ResolvedAgent:
        cached = self._cards.get(entry.name)
        if cached is not None:
            return ResolvedAgent(entry=entry, card=cached, error=None)
        try:
            card = await self._fetch_one(entry)
        except AgentUnreachableError as exc:
            return ResolvedAgent(entry=entry, card=None, error=str(exc))
        self._cards[entry.name] = card
        return ResolvedAgent(entry=entry, card=card, error=None)

    async def add(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None,
        persist: bool,
    ) -> ResolvedAgent:
        entry = AgentEntry(name=name, url=url.rstrip("/"), headers=headers or {})
        card = await self._fetch_one(entry)

        self._registry.agents[name] = entry
        self._cards[name] = card

        if persist and self._registry.path is not None:
            save_agent(self._registry.path, entry)

        return ResolvedAgent(entry=entry, card=card, error=None)

    def clear_cache(self) -> None:
        self._cards.clear()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_registry.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/registry.py tests/test_registry.py
git commit -m "feat: add agent card registry with lazy caching

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 3: Response normalization

Flattening protobuf into plain data is separable from making network calls, and it is where the subtle bugs live. It gets its own task and tests.

**Files:**
- Create: `src/mcp_a2a_bridge/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `TERMINAL_STATES: set[int]`, `BLOCKING_STATES: set[int]`, `state_name(state: int) -> str`, `is_done(state: int) -> bool`, `summarize_part(part: Part) -> str`, `card_summary(card: AgentCard) -> dict`, `A2AResult(state: str, text: str, task_id: str | None, context_id: str | None, done: bool)` with `to_dict() -> dict`.

`state_name` strips the `TASK_STATE_` prefix and lowercases, so `TASK_STATE_INPUT_REQUIRED` becomes `input_required`. Hosts see readable states, never raw integers.

- [ ] **Step 1: Write the failing normalization tests**

Create `tests/test_client.py`:

```python
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    TaskState,
)
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Value

from mcp_a2a_bridge.client import (
    A2AResult,
    BLOCKING_STATES,
    TERMINAL_STATES,
    card_summary,
    is_done,
    state_name,
    summarize_part,
)


def test_state_name_strips_prefix_and_lowercases():
    assert state_name(TaskState.TASK_STATE_COMPLETED) == "completed"
    assert state_name(TaskState.TASK_STATE_INPUT_REQUIRED) == "input_required"
    assert state_name(TaskState.TASK_STATE_WORKING) == "working"


def test_terminal_and_blocking_state_membership():
    assert TaskState.TASK_STATE_COMPLETED in TERMINAL_STATES
    assert TaskState.TASK_STATE_FAILED in TERMINAL_STATES
    assert TaskState.TASK_STATE_CANCELED in TERMINAL_STATES
    assert TaskState.TASK_STATE_REJECTED in TERMINAL_STATES
    assert TaskState.TASK_STATE_INPUT_REQUIRED in BLOCKING_STATES
    assert TaskState.TASK_STATE_AUTH_REQUIRED in BLOCKING_STATES
    assert TaskState.TASK_STATE_WORKING not in TERMINAL_STATES
    assert TaskState.TASK_STATE_WORKING not in BLOCKING_STATES


def test_is_done_covers_terminal_and_blocking():
    assert is_done(TaskState.TASK_STATE_COMPLETED) is True
    assert is_done(TaskState.TASK_STATE_INPUT_REQUIRED) is True
    assert is_done(TaskState.TASK_STATE_WORKING) is False


def test_summarize_text_part_returns_text():
    assert summarize_part(Part(text="hello")) == "hello"


def test_summarize_raw_part_never_leaks_bytes():
    part = Part(raw=b"\x89PNG\r\n", media_type="image/png", filename="pic.png")
    assert summarize_part(part) == "[file: pic.png, image/png, 6 bytes]"


def test_summarize_url_part():
    part = Part(url="https://example.com/f.pdf", media_type="application/pdf")
    assert summarize_part(part) == "[file: https://example.com/f.pdf, application/pdf]"


def test_summarize_data_part_inlines_json():
    part = Part(data=ParseDict({"key": "value"}, Value()))
    assert summarize_part(part) == '{"key": "value"}'


def test_card_summary_flattens_useful_fields():
    card = AgentCard(
        name="Planner",
        description="Plans things",
        version="1.2.3",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="http://localhost:9001/",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="plan",
                name="Plan",
                description="Make a plan",
                tags=["planning"],
                examples=["plan a trip"],
            )
        ],
    )
    summary = card_summary(card)

    assert summary["name"] == "Planner"
    assert summary["version"] == "1.2.3"
    assert summary["url"] == "http://localhost:9001/"
    assert summary["streaming"] is True
    assert summary["skills"] == [
        {
            "id": "plan",
            "name": "Plan",
            "description": "Make a plan",
            "tags": ["planning"],
            "examples": ["plan a trip"],
        }
    ]


def test_card_summary_handles_card_with_no_interfaces():
    summary = card_summary(AgentCard(name="Bare", description="d", version="1"))
    assert summary["url"] is None
    assert summary["skills"] == []


def test_result_to_dict_omits_empty_ids():
    result = A2AResult(
        state="completed", text="hi", task_id=None, context_id=None, done=True
    )
    assert result.to_dict() == {"state": "completed", "text": "hi", "done": True}


def test_result_to_dict_includes_ids_when_present():
    result = A2AResult(
        state="working", text="", task_id="t1", context_id="c1", done=False
    )
    assert result.to_dict() == {
        "state": "working",
        "text": "",
        "done": False,
        "task_id": "t1",
        "context_id": "c1",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_client.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'mcp_a2a_bridge.client'`

- [ ] **Step 3: Implement the normalization half of client.py**

Create `src/mcp_a2a_bridge/client.py`:

```python
"""A2A client calls and protobuf-to-plain-data normalization."""

from __future__ import annotations

from dataclasses import dataclass

from a2a.types import AgentCard, Part, TaskState
from google.protobuf.json_format import MessageToJson

TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}

BLOCKING_STATES = {
    TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}


def state_name(state: int) -> str:
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


def is_done(state: int) -> bool:
    """Stop consuming: the task is finished or is waiting on the caller."""
    return state in TERMINAL_STATES or state in BLOCKING_STATES


def summarize_part(part: Part) -> str:
    # The Part oneof is named "content", not "part".
    which = part.WhichOneof("content")

    if which == "text":
        return part.text

    if which == "raw":
        label = part.filename or "unnamed"
        media = part.media_type or "application/octet-stream"
        return f"[file: {label}, {media}, {len(part.raw)} bytes]"

    if which == "url":
        media = part.media_type or "application/octet-stream"
        return f"[file: {part.url}, {media}]"

    if which == "data":
        return MessageToJson(part.data, indent=0).replace("\n", "")

    return ""


def card_summary(card: AgentCard) -> dict:
    url = None
    if card.supported_interfaces:
        url = card.supported_interfaces[0].url

    return {
        "name": card.name,
        "description": card.description,
        "version": card.version,
        "url": url,
        "streaming": bool(card.capabilities.streaming),
        "input_modes": list(card.default_input_modes),
        "output_modes": list(card.default_output_modes),
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "examples": list(skill.examples),
            }
            for skill in card.skills
        ],
    }


@dataclass
class A2AResult:
    state: str
    text: str
    task_id: str | None
    context_id: str | None
    done: bool

    def to_dict(self) -> dict:
        result: dict = {"state": self.state, "text": self.text, "done": self.done}
        if self.task_id:
            result["task_id"] = self.task_id
        if self.context_id:
            result["context_id"] = self.context_id
        return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_client.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/client.py tests/test_client.py
git commit -m "feat: normalize A2A protobuf responses to plain data

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 4: A2A calls with timeout handling

**Files:**
- Modify: `src/mcp_a2a_bridge/client.py` (append; do not change Task 3 functions)
- Test: `tests/test_client.py` (append)

**Interfaces:**
- Consumes: `AgentEntry` from config; `A2AResult`, `is_done`, `state_name`, `summarize_part` from Task 3.
- Produces:
  - `async consume_stream(chunks: AsyncIterator[StreamResponse], task_id: str | None, context_id: str | None, timeout_s: float = 60) -> A2AResult`
  - `async send_message(entry, card, message: str, task_id=None, context_id=None, timeout_s: int = 60) -> A2AResult`
  - `async get_task(entry, card, task_id: str) -> A2AResult`
  - `async cancel_task(entry, card, task_id: str) -> A2AResult`

Splitting `consume_stream` out is what makes the streaming logic testable without a network: tests feed it `StreamResponse` objects directly.

- [ ] **Step 1: Write the failing stream-consumption tests**

Append to `tests/test_client.py`:

```python
import asyncio

from a2a.types import (
    Message,
    Role,
    StreamResponse,
    Task,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from mcp_a2a_bridge.client import consume_stream


async def as_stream(items):
    for item in items:
        yield item


def task_chunk(task_id, state):
    return StreamResponse(
        task=Task(id=task_id, context_id="c1", status=TaskStatus(state=state))
    )


def status_chunk(task_id, state, text=None):
    message = None
    if text is not None:
        message = Message(role=Role.ROLE_AGENT, parts=[Part(text=text)])
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id=task_id,
            context_id="c1",
            status=TaskStatus(state=state, message=message),
        )
    )


async def test_consume_stream_accumulates_text_until_completed():
    chunks = as_stream([
        task_chunk("t1", TaskState.TASK_STATE_SUBMITTED),
        status_chunk("t1", TaskState.TASK_STATE_WORKING, "thinking"),
        status_chunk("t1", TaskState.TASK_STATE_COMPLETED, "done"),
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.state == "completed"
    assert result.text == "thinking\ndone"
    assert result.task_id == "t1"
    assert result.context_id == "c1"
    assert result.done is True


async def test_consume_stream_stops_at_input_required():
    chunks = as_stream([
        task_chunk("t1", TaskState.TASK_STATE_SUBMITTED),
        status_chunk("t1", TaskState.TASK_STATE_INPUT_REQUIRED, "which city?"),
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.state == "input_required"
    assert result.text == "which city?"
    assert result.task_id == "t1"
    assert result.done is True


async def test_consume_stream_reports_failed_as_data_not_exception():
    chunks = as_stream([
        task_chunk("t1", TaskState.TASK_STATE_SUBMITTED),
        status_chunk("t1", TaskState.TASK_STATE_FAILED, "upstream exploded"),
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.state == "failed"
    assert "upstream exploded" in result.text
    assert result.done is True


async def test_consume_stream_handles_direct_message_reply():
    chunks = as_stream([
        StreamResponse(message=Message(role=Role.ROLE_AGENT, parts=[Part(text="hi")]))
    ])
    result = await consume_stream(chunks, task_id=None, context_id=None)

    assert result.text == "hi"
    assert result.state == "completed"
    assert result.done is True


async def test_consume_stream_timeout_returns_working_with_task_id():
    async def slow():
        yield task_chunk("t1", TaskState.TASK_STATE_SUBMITTED)
        await asyncio.sleep(5)
        yield status_chunk("t1", TaskState.TASK_STATE_COMPLETED, "never seen")

    result = await consume_stream(slow(), task_id=None, context_id=None, timeout_s=0.1)

    assert result.state == "working"
    assert result.task_id == "t1"
    assert result.done is False
    assert "never seen" not in result.text


async def test_consume_stream_preserves_incoming_task_id_when_stream_is_silent():
    chunks = as_stream([
        StreamResponse(message=Message(role=Role.ROLE_AGENT, parts=[Part(text="ok")]))
    ])
    result = await consume_stream(chunks, task_id="given", context_id="ctx")

    assert result.task_id == "given"
    assert result.context_id == "ctx"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_client.py -k consume_stream -v`
Expected: collection error — `ImportError: cannot import name 'consume_stream'`

- [ ] **Step 3: Implement the call half of client.py**

Add these imports at the top of `src/mcp_a2a_bridge/client.py`, alongside the existing ones:

```python
import asyncio
from collections.abc import AsyncIterator

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import new_text_message
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
)

from mcp_a2a_bridge.config import AgentEntry
```

Then append to the same file:

```python
def _message_text(message: Message) -> str:
    return "\n".join(
        summary for summary in (summarize_part(p) for p in message.parts) if summary
    )


def _task_result(task: Task) -> A2AResult:
    text = _message_text(task.status.message) if task.status.HasField("message") else ""
    if not text:
        pieces = []
        for artifact in task.artifacts:
            for part in artifact.parts:
                summary = summarize_part(part)
                if summary:
                    pieces.append(summary)
        text = "\n".join(pieces)

    return A2AResult(
        state=state_name(task.status.state),
        text=text,
        task_id=task.id or None,
        context_id=task.context_id or None,
        done=is_done(task.status.state),
    )


async def consume_stream(
    chunks: AsyncIterator[StreamResponse],
    task_id: str | None,
    context_id: str | None,
    timeout_s: float = 60,
) -> A2AResult:
    """Accumulate a StreamResponse iterator into one flat result.

    Returns rather than raises on timeout: a still-running task is a normal
    outcome the caller can poll on.
    """
    pieces: list[str] = []
    state: int | None = None
    timed_out = False

    try:
        async with asyncio.timeout(timeout_s):
            async for chunk in chunks:
                which = chunk.WhichOneof("payload")

                if which == "task":
                    task_id = chunk.task.id or task_id
                    context_id = chunk.task.context_id or context_id
                    state = chunk.task.status.state
                    if chunk.task.status.HasField("message"):
                        text = _message_text(chunk.task.status.message)
                        if text:
                            pieces.append(text)

                elif which == "status_update":
                    update = chunk.status_update
                    task_id = update.task_id or task_id
                    context_id = update.context_id or context_id
                    state = update.status.state
                    if update.status.HasField("message"):
                        text = _message_text(update.status.message)
                        if text:
                            pieces.append(text)

                elif which == "artifact_update":
                    update = chunk.artifact_update
                    task_id = update.task_id or task_id
                    context_id = update.context_id or context_id
                    for part in update.artifact.parts:
                        text = summarize_part(part)
                        if text:
                            pieces.append(text)

                elif which == "message":
                    text = _message_text(chunk.message)
                    if text:
                        pieces.append(text)
                    task_id = chunk.message.task_id or task_id
                    context_id = chunk.message.context_id or context_id
                    if state is None:
                        state = TaskState.TASK_STATE_COMPLETED

                if state is not None and is_done(state):
                    break
    except TimeoutError:
        timed_out = True

    if timed_out:
        state = TaskState.TASK_STATE_WORKING
    elif state is None:
        state = TaskState.TASK_STATE_COMPLETED

    return A2AResult(
        state=state_name(state),
        text="\n".join(pieces),
        task_id=task_id,
        context_id=context_id,
        done=is_done(state),
    )


def _http_client(entry: AgentEntry) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=entry.headers, timeout=None)


def _build_client(http_client: httpx.AsyncClient, card: AgentCard):
    config = ClientConfig(
        httpx_client=http_client,
        accepted_output_modes=["text/plain"],
    )
    return ClientFactory(config=config).create(card)


async def send_message(
    entry: AgentEntry,
    card: AgentCard,
    message: str,
    task_id: str | None = None,
    context_id: str | None = None,
    timeout_s: int = 60,
) -> A2AResult:
    async with _http_client(entry) as http_client:
        client = _build_client(http_client, card)
        request = SendMessageRequest(
            message=new_text_message(
                message,
                role=Role.ROLE_USER,
                task_id=task_id,
                context_id=context_id,
            )
        )
        return await consume_stream(
            client.send_message(request),
            task_id=task_id,
            context_id=context_id,
            timeout_s=timeout_s,
        )


async def get_task(entry: AgentEntry, card: AgentCard, task_id: str) -> A2AResult:
    async with _http_client(entry) as http_client:
        client = _build_client(http_client, card)
        task = await client.get_task(GetTaskRequest(id=task_id))
        return _task_result(task)


async def cancel_task(entry: AgentEntry, card: AgentCard, task_id: str) -> A2AResult:
    async with _http_client(entry) as http_client:
        client = _build_client(http_client, card)
        task = await client.cancel_task(CancelTaskRequest(id=task_id))
        return _task_result(task)
```

`GetTaskRequest(id=...)` and `CancelTaskRequest(id=...)` are correct. `task_id=` is not a field on these messages and raises `ValueError`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_client.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp_a2a_bridge/client.py tests/test_client.py
git commit -m "feat: add A2A send, poll and cancel with bounded timeout

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 5: MCP server and tools

**Files:**
- Create: `src/mcp_a2a_bridge/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `resolve_config_path`, `load_registry`, `ConfigError` from config; `AgentRegistry` from registry; `send_message`, `get_task`, `cancel_task`, `card_summary` from client.
- Produces: `build_server(registry: AgentRegistry) -> MCPServer`, `main() -> None`.

`build_server` takes an injected registry so tests construct a server over a fake registry with no network and no config file.

- [ ] **Step 1: Write the failing server tests**

Create `tests/test_server.py`:

```python
import pytest
from a2a.types import AgentCard

from mcp_a2a_bridge.config import AgentEntry, Registry
from mcp_a2a_bridge.registry import AgentRegistry
from mcp_a2a_bridge.server import build_server

EXPECTED_TOOLS = {
    "a2a_list_agents",
    "a2a_send_message",
    "a2a_get_task",
    "a2a_cancel_task",
    "a2a_add_agent",
}


def payload(result):
    """Extract the structured dict from an MCP call_tool result."""
    if isinstance(result, tuple):
        result = result[1]
    if isinstance(result, dict):
        return result
    return result.structured_content


def fake_registry(**agents):
    async def fetch(entry):
        return AgentCard(name=entry.name, description="d", version="1.0.0")

    return AgentRegistry(
        Registry(
            path=None,
            agents={n: AgentEntry(name=n, url=u, headers={}) for n, u in agents.items()},
        ),
        fetch_card=fetch,
    )


async def test_server_registers_exactly_five_tools():
    server = build_server(fake_registry())
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_every_tool_has_a_description():
    server = build_server(fake_registry())
    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"


async def test_list_agents_reports_reachability_instead_of_failing():
    async def fetch(entry):
        if entry.name == "bad":
            raise RuntimeError("refused")
        return AgentCard(name=entry.name, description="d", version="1.0.0")

    registry = AgentRegistry(
        Registry(
            path=None,
            agents={
                "good": AgentEntry(name="good", url="http://x", headers={}),
                "bad": AgentEntry(name="bad", url="http://y", headers={}),
            },
        ),
        fetch_card=fetch,
    )
    server = build_server(registry)

    agents = {a["name"]: a for a in payload(await server.call_tool("a2a_list_agents", {}))["agents"]}

    assert agents["good"]["reachable"] is True
    assert agents["bad"]["reachable"] is False
    assert "refused" in agents["bad"]["error"]


async def test_unknown_agent_is_a_tool_error():
    server = build_server(fake_registry(planner="http://x"))
    with pytest.raises(Exception) as exc:
        await server.call_tool("a2a_send_message", {"agent": "nope", "message": "hi"})
    assert "nope" in str(exc.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_server.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'mcp_a2a_bridge.server'`

- [ ] **Step 3: Implement server.py**

Create `src/mcp_a2a_bridge/server.py`:

```python
"""MCP server exposing remote A2A agents as tools."""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer

from mcp_a2a_bridge import client
from mcp_a2a_bridge.config import ConfigError, load_registry, resolve_config_path
from mcp_a2a_bridge.registry import AgentRegistry

INSTRUCTIONS = (
    "Call remote A2A (Agent2Agent) agents. Start with a2a_list_agents to see "
    "which agents exist and what they can do. Use a2a_send_message to ask one "
    "to do something. If a call returns done=false with a task_id, the agent is "
    "still working: poll a2a_get_task with that id. If it returns "
    'state="input_required", reply by calling a2a_send_message again with the '
    "same task_id."
)


def build_server(registry: AgentRegistry) -> MCPServer:
    server = MCPServer(name="a2a-bridge", instructions=INSTRUCTIONS)

    @server.tool()
    async def a2a_list_agents(refresh: bool = False) -> dict:
        """List configured A2A agents with their skills and reachability.

        Set refresh=true to re-fetch agent cards that were previously cached.
        """
        agents = []
        for item in await registry.resolve_all(refresh=refresh):
            summary = {
                "name": item.entry.name,
                "configured_url": item.entry.url,
                "reachable": item.reachable,
            }
            if item.card is not None:
                summary.update(client.card_summary(item.card))
                summary["name"] = item.entry.name
            else:
                summary["error"] = item.error
            agents.append(summary)

        return {
            "agents": agents,
            "config_path": str(registry.config_path) if registry.config_path else None,
        }

    @server.tool()
    async def a2a_send_message(
        agent: str,
        message: str,
        task_id: str | None = None,
        context_id: str | None = None,
        timeout_s: int = 60,
    ) -> dict:
        """Send a message to an A2A agent and return its reply.

        Pass task_id to continue an existing task, for example to answer an
        agent that returned state="input_required". If the agent is still
        working when timeout_s elapses, this returns done=false with a task_id
        to poll rather than blocking.
        """
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.send_message(
            entry,
            card,
            message,
            task_id=task_id,
            context_id=context_id,
            timeout_s=timeout_s,
        )
        return result.to_dict()

    @server.tool()
    async def a2a_get_task(agent: str, task_id: str) -> dict:
        """Get the current state and output of a previously started A2A task."""
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.get_task(entry, card, task_id)
        return result.to_dict()

    @server.tool()
    async def a2a_cancel_task(agent: str, task_id: str) -> dict:
        """Cancel a running A2A task and return its final state."""
        entry = registry.entry(agent)
        card = await registry.card(agent)
        result = await client.cancel_task(entry, card, task_id)
        return result.to_dict()

    @server.tool()
    async def a2a_add_agent(
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
        persist: bool = True,
    ) -> dict:
        """Register a new A2A agent by its base URL.

        The agent card is fetched first, so an unreachable URL is rejected
        instead of being saved. With persist=true the agent is written to the
        shared registry file and stays available in future sessions.
        """
        resolved = await registry.add(name, url, headers, persist)
        summary = client.card_summary(resolved.card)
        summary["name"] = name
        summary["persisted"] = persist and registry.config_path is not None
        return summary

    return server


def main() -> None:
    try:
        registry = AgentRegistry(load_registry(resolve_config_path()))
    except ConfigError as exc:
        print(f"mcp-a2a-bridge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    build_server(registry).run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_server.py -v`
Expected: 4 passed

If `payload` cannot find the dict, print `type(result)` and `result` once, then adjust `payload` to the real shape `MCPServer.call_tool` returns in mcp 2.0. Do not weaken the assertions.

- [ ] **Step 5: Verify the server actually starts over stdio**

```bash
cd ~/WorkSpace/mcp-a2a-bridge
printf '' | timeout 5 .venv/bin/mcp-a2a-bridge ; echo "exit=$?"
```

Expected: no Python traceback. `exit=0` or `exit=124` are both fine.

- [ ] **Step 6: Commit**

```bash
git add src/mcp_a2a_bridge/server.py tests/test_server.py
git commit -m "feat: expose A2A agents as five MCP tools

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 6: Integration test against a real A2A agent

This is the test that catches an `a2a-sdk` upgrade breaking us. Everything before it could pass while the bridge fails against a real agent.

**Files:**
- Create: `tests/echo_agent.py`, `tests/test_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `build_card(port: int) -> AgentCard`, `build_app(card: AgentCard) -> FastAPI`.

The echo agent below is verified working against a2a-sdk 1.1.2. One detail is load-bearing: the executor must enqueue the `Task` **before** any status update, or the server raises `InvalidAgentResponseError: Agent should enqueue Task before TaskStatusUpdateEvent event`.

- [ ] **Step 1: Create the echo agent**

Create `tests/echo_agent.py`:

```python
"""A real in-process A2A agent used to prove the bridge speaks actual A2A."""

from __future__ import annotations

import asyncio

from a2a.helpers import get_message_text, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part
from fastapi import FastAPI


def build_card(port: int) -> AgentCard:
    return AgentCard(
        name="Echo",
        description="Echoes text back",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://127.0.0.1:{port}/",
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echo the text back",
                tags=["test"],
                examples=["say hello"],
            )
        ],
    )


class EchoExecutor(AgentExecutor):
    """Echoes input.

    "slow" sleeps so the bridge's timeout path can be tested.
    "ask" stops at input_required so multi-turn continuation can be tested.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        text = get_message_text(context.message)

        if text == "slow":
            await asyncio.sleep(30)

        if text == "ask":
            await updater.requires_input(
                updater.new_agent_message([Part(text="which city?")])
            )
            return

        await updater.complete(updater.new_agent_message([Part(text=f"echo: {text}")]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def build_app(card: AgentCard) -> FastAPI:
    handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    return app
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/test_integration.py`:

```python
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from mcp_a2a_bridge.config import AgentEntry, Registry
from mcp_a2a_bridge.registry import AgentRegistry
from mcp_a2a_bridge.server import build_server
from tests.echo_agent import build_app, build_card


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def payload(result):
    if isinstance(result, tuple):
        result = result[1]
    if isinstance(result, dict):
        return result
    return result.structured_content


@pytest.fixture(scope="module")
def echo_agent_url():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(build_card(port)),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/.well-known/agent-card.json", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    else:
        pytest.fail("echo agent did not start")

    yield url

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def bridge(echo_agent_url, tmp_path):
    registry = AgentRegistry(Registry(path=tmp_path / "agents.json", agents={}))
    return build_server(registry), registry, echo_agent_url


async def test_add_then_list_shows_real_skills(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    agent = payload(await server.call_tool("a2a_list_agents", {}))["agents"][0]

    assert agent["name"] == "echo"
    assert agent["reachable"] is True
    assert agent["streaming"] is True
    assert agent["skills"][0]["id"] == "echo"


async def test_send_message_round_trip(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    result = payload(
        await server.call_tool(
            "a2a_send_message", {"agent": "echo", "message": "hello world"}
        )
    )

    assert result["state"] == "completed"
    assert result["text"] == "echo: hello world"
    assert result["done"] is True


async def test_slow_agent_returns_task_id_then_can_be_polled(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    started = payload(
        await server.call_tool(
            "a2a_send_message", {"agent": "echo", "message": "slow", "timeout_s": 1}
        )
    )

    assert started["done"] is False
    assert started["state"] == "working"
    assert started["task_id"]

    polled = payload(
        await server.call_tool(
            "a2a_get_task", {"agent": "echo", "task_id": started["task_id"]}
        )
    )
    assert polled["state"] in {"working", "submitted"}


async def test_input_required_can_be_continued_with_same_task_id(bridge):
    server, _registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    first = payload(
        await server.call_tool("a2a_send_message", {"agent": "echo", "message": "ask"})
    )

    assert first["state"] == "input_required"
    assert first["text"] == "which city?"

    second = payload(
        await server.call_tool(
            "a2a_send_message",
            {"agent": "echo", "message": "Bangkok", "task_id": first["task_id"]},
        )
    )
    assert second["state"] == "completed"
    assert second["text"] == "echo: Bangkok"


async def test_unreachable_agent_does_not_break_other_agents(bridge):
    server, registry, url = bridge

    await server.call_tool("a2a_add_agent", {"name": "echo", "url": url})
    registry._registry.agents["dead"] = AgentEntry(
        name="dead", url="http://127.0.0.1:1", headers={}
    )

    listed = payload(await server.call_tool("a2a_list_agents", {}))
    agents = {a["name"]: a for a in listed["agents"]}

    assert agents["echo"]["reachable"] is True
    assert agents["dead"]["reachable"] is False
    assert agents["dead"]["error"]
```

- [ ] **Step 3: Run the integration tests**

Run: `.venv/bin/pytest tests/test_integration.py -v`
Expected: 5 passed

Likely failures and fixes:
- `InvalidAgentResponseError: Agent should enqueue Task before TaskStatusUpdateEvent` — the executor skipped `enqueue_event(task)`. Restore it.
- `test_slow_agent...` returns `completed` — the sleep is shorter than `timeout_s`. Keep sleep at 30s and `timeout_s` at 1.
- Fixture times out — raise `log_level` to `"info"` to see why uvicorn did not bind.

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: 43 passed

- [ ] **Step 5: Commit**

```bash
git add tests/echo_agent.py tests/test_integration.py
git commit -m "test: add real A2A round-trip integration coverage

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

### Task 7: Documentation and host registration

**Files:**
- Create: `README.md`
- Create at runtime (not committed): `~/.config/a2a-bridge/agents.json`

**Interfaces:**
- Consumes: the working `mcp-a2a-bridge` console script.
- Produces: no code.

- [ ] **Step 1: Write the README**

Create `README.md`:

````markdown
# mcp-a2a-bridge

An MCP server that lets coding agents — GitHub Copilot CLI, Claude Code, Codex,
and Hermes — call remote [A2A](https://a2a-protocol.org) agents.

The bridge is an A2A **client**. It does not expose your coding agent as an A2A
server.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Install

```bash
cd ~/WorkSpace/mcp-a2a-bridge
uv venv --python 3.11
uv pip install -e ".[dev]"
```

## Configure agents

The registry is shared by every host. Resolution order:

1. `$A2A_BRIDGE_CONFIG`
2. `./.a2a-agents.json` in the current directory
3. `~/.config/a2a-bridge/agents.json`

```json
{
  "agents": {
    "planner": {
      "url": "http://localhost:9001",
      "headers": {}
    }
  }
}
```

`url` is the agent's base URL; the bridge appends
`/.well-known/agent-card.json`. You can also add agents at runtime with the
`a2a_add_agent` tool, which writes back to this file.

## Register with your host

All four hosts run the same command.

**GitHub Copilot CLI** — add to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "a2a": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "/Users/YOU/WorkSpace/mcp-a2a-bridge", "mcp-a2a-bridge"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add a2a -- uvx --from ~/WorkSpace/mcp-a2a-bridge mcp-a2a-bridge
```

**Codex** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.a2a]
command = "uvx"
args = ["--from", "/Users/YOU/WorkSpace/mcp-a2a-bridge", "mcp-a2a-bridge"]
```

**Hermes:**

```bash
hermes mcp add
```

Choose a stdio server and supply the same command and args.

## Tools

| Tool | Purpose |
|---|---|
| `a2a_list_agents` | List agents with skills and reachability |
| `a2a_send_message` | Send a message; continue a task with `task_id` |
| `a2a_get_task` | Poll a task started earlier |
| `a2a_cancel_task` | Cancel a running task |
| `a2a_add_agent` | Register an agent by URL |

### Long-running tasks

`a2a_send_message` blocks up to `timeout_s` (default 60). If the agent is still
working it returns `done: false` with a `task_id`; poll `a2a_get_task`. It never
hangs the host.

### Multi-turn

An agent that returns `state: "input_required"` is answered by calling
`a2a_send_message` again with the same `task_id`.

## Development

```bash
.venv/bin/pytest -v
```

`tests/test_integration.py` runs a real A2A agent in-process and exercises the
bridge end to end.
````

- [ ] **Step 2: Verify the bridge runs via uvx exactly as the README says**

```bash
cd /tmp
printf '' | timeout 10 uvx --from ~/WorkSpace/mcp-a2a-bridge mcp-a2a-bridge ; echo "exit=$?"
```

Expected: no traceback. If `uvx` cannot build the package, confirm `[build-system]` and `[tool.hatch.build.targets.wheel]` match Task 1.

- [ ] **Step 3: Create a starter registry**

```bash
mkdir -p ~/.config/a2a-bridge
[ -f ~/.config/a2a-bridge/agents.json ] || echo '{"agents": {}}' > ~/.config/a2a-bridge/agents.json
cat ~/.config/a2a-bridge/agents.json
```

Expected: `{"agents": {}}`

- [ ] **Step 4: Register with Copilot CLI and verify the tools appear**

Add the `a2a` entry to `~/.copilot/mcp-config.json` as shown in the README, preserving the existing servers. Then in a new Copilot CLI session run `/mcp` and confirm the `a2a` server lists five tools:
`a2a_list_agents`, `a2a_send_message`, `a2a_get_task`, `a2a_cancel_task`, `a2a_add_agent`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add install and host registration guide

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Verification against spec success criteria

| Spec criterion | Verified by |
|---|---|
| 1. All four hosts list five tools | Task 5 Step 5, Task 7 Steps 2 and 4, `test_server_registers_exactly_five_tools` |
| 2. `a2a_list_agents` shows skills; `a2a_send_message` returns a reply | `test_add_then_list_shows_real_skills`, `test_send_message_round_trip` |
| 3. Slow agent returns `task_id`; `a2a_get_task` polls it | `test_slow_agent_returns_task_id_then_can_be_polled`, `test_consume_stream_timeout_returns_working_with_task_id` |
| 4. `input_required` driven to completion with same `task_id` | `test_input_required_can_be_continued_with_same_task_id` |
| 5. Unreachable agent errors clearly without affecting others or startup | `test_unreachable_agent_does_not_break_other_agents`, `test_resolve_all_reports_failures_without_raising`, `test_unknown_agent_is_a_tool_error` |
| Malformed registry fails fast | `test_invalid_json_raises_config_error`, `main()` in Task 5 |
| Non-text parts summarized, never base64 | `test_summarize_raw_part_never_leaks_bytes`, `test_summarize_url_part`, `test_summarize_data_part_inlines_json` |
| Shared registry across hosts | Task 1 `resolve_config_path`, Task 7 README |
| `add_agent` rejects unreachable URLs | `test_add_does_not_persist_unreachable_agent` |
| Config resolution order | `test_env_override_wins`, `test_project_local_file_used_when_present`, `test_falls_back_to_default_path` |
