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

        if persist and self._registry.path is not None:
            save_agent(self._registry.path, entry)

        self._registry.agents[name] = entry
        self._cards[name] = card

        return ResolvedAgent(entry=entry, card=card, error=None)

    def clear_cache(self) -> None:
        self._cards.clear()
