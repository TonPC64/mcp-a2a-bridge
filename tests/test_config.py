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
