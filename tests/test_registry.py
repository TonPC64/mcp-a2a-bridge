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
