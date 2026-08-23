"""Resolve and cache A2A agent cards."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any

import httpx
from a2a.client import A2ACardResolver
from a2a.types import AgentCard

from mcp_a2a_bridge import client
from mcp_a2a_bridge.config import AgentEntry, Registry, save_agent
from mcp_a2a_bridge.snapshots import SnapshotSubscribers

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


def resolved_agent_summary(item: ResolvedAgent) -> dict:
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
    return summary


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
        self._lock = threading.RLock()
        self._subscribers = SnapshotSubscribers()
        self._summaries: dict[str, dict] = {}

    @property
    def config_path(self) -> Path | None:
        return self._registry.path

    def names(self) -> list[str]:
        with self._lock:
            return list(self._registry.agents)

    def entry(self, name: str) -> AgentEntry:
        with self._lock:
            try:
                return self._registry.agents[name]
            except KeyError:
                known = ", ".join(sorted(self._registry.agents)) or "(none configured)"
                raise UnknownAgentError(
                    f'Unknown agent "{name}". Configured agents: {known}'
                ) from None

    async def card(self, name: str) -> AgentCard:
        entry = self.entry(name)
        with self._lock:
            cached = self._cards.get(name)
        if cached is not None:
            return cached

        card = await self._fetch_one(entry)
        with self._lock:
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

        with self._lock:
            entries = list(self._registry.agents.values())
        results = await asyncio.gather(*(self._resolve_one(e) for e in entries))
        resolved = list(results)
        summaries = [resolved_agent_summary(item) for item in resolved]
        with self._lock:
            self._summaries = {summary["name"]: summary for summary in summaries}
        self._subscribers.publish({"agents": summaries})
        return resolved

    async def _resolve_one(self, entry: AgentEntry) -> ResolvedAgent:
        with self._lock:
            cached = self._cards.get(entry.name)
        if cached is not None:
            return ResolvedAgent(entry=entry, card=cached, error=None)
        try:
            card = await self._fetch_one(entry)
        except AgentUnreachableError as exc:
            return ResolvedAgent(entry=entry, card=None, error=str(exc))
        with self._lock:
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

        if persist and self._registry.path is not None:
            save_agent(self._registry.path, entry)

        with self._lock:
            self._registry.agents[name] = entry
            self._cards[name] = card
        resolved = ResolvedAgent(entry=entry, card=card, error=None)
        summary = resolved_agent_summary(resolved)
        with self._lock:
            self._summaries[name] = summary
            snapshot = {
                "agents": [
                    self._summaries[configured.name]
                    for configured in self._registry.agents.values()
                    if configured.name in self._summaries
                ]
            }
        self._subscribers.publish(snapshot)
        return resolved

    def clear_cache(self) -> None:
        with self._lock:
            self._cards.clear()

    def subscribe(self) -> Queue[dict[str, Any]]:
        return self._subscribers.subscribe()

    def unsubscribe(self, subscriber: Queue[dict[str, Any]]) -> None:
        self._subscribers.unsubscribe(subscriber)

    @property
    def subscriber_count(self) -> int:
        return self._subscribers.count
