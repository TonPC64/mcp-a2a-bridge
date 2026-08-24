"""Read Hermes' append-only A2A audit log without changing Hermes state."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mcp_a2a_bridge.activity import TEXT_PREVIEW_LIMIT

DEFAULT_HERMES_AUDIT_PATH = Path.home() / ".hermes" / "a2a_audit.jsonl"
MAX_AUDIT_BYTES = 1_000_000
MAX_AUDIT_LINES = 2_000
MAX_TASKS = 500

_BEARER = re.compile(r"(?i)\b(bearer\s+)[^\s,;]+")
_SECRET = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|token|password|secret|authorization)[\"']?\s*[=:]\s*)[^\s,;]+"
)
_URL_CREDENTIALS = re.compile(r"//[^/\s:@]+:[^@\s/]+@")
_KNOWN_TOKEN = re.compile(r"\b(?:sk|gh[pousr])-[A-Za-z0-9_-]{12,}\b|\bAKIA[A-Z0-9]{16}\b")


def resolve_hermes_audit_path() -> Path:
    """Return the read-only Hermes audit location for this user profile."""
    override = os.environ.get("A2A_BRIDGE_HERMES_AUDIT")
    if override:
        return Path(override).expanduser()
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        return Path(hermes_home).expanduser() / "a2a_audit.jsonl"
    return DEFAULT_HERMES_AUDIT_PATH


def _safe_summary(summary: str) -> str:
    summary = _URL_CREDENTIALS.sub("//[REDACTED]@", summary)
    summary = _BEARER.sub(r"\1[REDACTED]", summary)
    summary = _SECRET.sub(r"\1[REDACTED]", summary)
    summary = _KNOWN_TOKEN.sub("[REDACTED]", summary)
    return summary.replace("\x00", "")[:TEXT_PREVIEW_LIMIT]


def _audit_lines(path: Path) -> list[str]:
    """Read a bounded complete-line tail, tolerating append/rotation races."""
    try:
        with path.open("rb") as audit:
            audit.seek(0, os.SEEK_END)
            start = max(0, audit.tell() - MAX_AUDIT_BYTES)
            audit.seek(start)
            if start:
                audit.readline()  # discard a line that began before the tail
            data = audit.read(MAX_AUDIT_BYTES)
    except OSError:
        return []
    if not data:
        return []
    if not data.endswith((b"\n", b"\r")):
        data = data.rsplit(b"\n", 1)[0] if b"\n" in data else b""
    return data.decode("utf-8", errors="replace").splitlines()[-MAX_AUDIT_LINES:]


def _normalize_peer(peer: str) -> str:
    parsed = urlsplit(peer.strip())
    if not parsed.scheme or not parsed.netloc:
        return peer.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def list_hermes_audit_entries(
    path: Path | None = None, agent_aliases: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Return the latest valid Hermes A2A exchanges in task API shape.

    Audit records prove a call was written, not that its remote task finished,
    so their state is deliberately ``recorded``.
    """
    latest: dict[str, dict[str, Any]] = {}
    for line in _audit_lines(path or resolve_hermes_audit_path()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("direction") not in {"inbound", "outbound"}:
            continue
        direction = row["direction"]
        task_id, timestamp, peer, summary = (
            row.get("task_id"),
            row.get("ts"),
            row.get("peer"),
            row.get("summary"),
        )
        if (
            not isinstance(task_id, str)
            or not task_id
            or len(task_id) > 256
            or not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
            or not math.isfinite(timestamp)
            or not isinstance(peer, str)
            or not peer
            or len(peer) > 512
            or not isinstance(summary, str)
        ):
            continue
        display_peer = (agent_aliases or {}).get(_normalize_peer(peer), peer)
        entry_id = f"hermes:{task_id}" if direction == "outbound" else f"hermes:inbound:{task_id}"
        latest[entry_id] = {
            "id": entry_id,
            "agent": display_peer,
            "kind": "a2a_call" if direction == "outbound" else "a2a_receive",
            "state": "recorded",
            "text": _safe_summary(summary),
            "created_at": float(timestamp),
            "updated_at": float(timestamp),
        }
    return sorted(latest.values(), key=lambda entry: entry["updated_at"], reverse=True)[:MAX_TASKS]


def merge_task_activity(
    bridge_entries: list[dict[str, Any]], hermes_entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge independent activity sources into the dashboard's bounded view."""
    latest = {
        entry["id"]: entry
        for entry in sorted(
            [*bridge_entries, *hermes_entries], key=lambda entry: entry["updated_at"]
        )
    }
    return sorted(latest.values(), key=lambda entry: entry["updated_at"], reverse=True)[:MAX_TASKS]
