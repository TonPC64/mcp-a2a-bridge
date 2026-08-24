"""SQLite-backed shared activity storage for the multi-process dashboard."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_ACTIVITY_DB = Path.home() / ".config" / "a2a-bridge" / "activity.sqlite3"


def resolve_activity_db_path() -> Path:
    """Where every bridge process writes to and the dashboard reads from.

    Single source of truth for A2A_BRIDGE_ACTIVITY_DB resolution -- imported
    by both dashboard_service.py and server.py so the default path can never
    drift between the writer side and the reader side.
    """
    override = os.environ.get("A2A_BRIDGE_ACTIVITY_DB")
    return Path(override).expanduser() if override else DEFAULT_ACTIVITY_DB


class SQLiteActivityStore:
    """Persist task activity entries so multiple bridge processes can share one log.

    Mirrors the upsert-by-id, TTL/LRU-eviction shape of
    ``mcp_a2a_bridge.sqlite_task_store.SQLiteTaskStore``. WAL mode is enabled
    so one bridge's write does not block another bridge's write, or the
    dashboard's concurrent reads.
    """

    def __init__(self, path: Path, maxsize: int = 500) -> None:
        self._path = Path(path).expanduser()
        self._maxsize = maxsize
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activity (
                    id TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    destination TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(activity)")}
            for name in ("source", "destination"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE activity ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        # busy_timeout is a per-connection setting (unlike journal_mode=WAL,
        # which persists in the DB file once set) and Python's sqlite3
        # default is ~5s, which would stall the caller for a full 5s before
        # raising under write contention -- now that a failure is survivable
        # (see ActivityLog.record()), fail fast instead.
        connection.execute("PRAGMA busy_timeout=1000")
        return connection

    def _evict(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM activity WHERE id IN (
                SELECT id FROM activity
                ORDER BY updated_at ASC
                LIMIT MAX(0, (SELECT COUNT(*) FROM activity) - ?)
            )
            """,
            (self._maxsize,),
        )

    def upsert(self, entry: dict) -> None:
        entry = {"source": "", "destination": "", **entry}
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO activity(id, agent, source, destination, kind, state, text, created_at, updated_at)
                VALUES (:id, :agent, :source, :destination, :kind, :state, :text, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    agent=excluded.agent,
                    source=excluded.source,
                    destination=excluded.destination,
                    kind=excluded.kind,
                    state=excluded.state,
                    text=excluded.text,
                    updated_at=excluded.updated_at
                """,
                entry,
            )
            self._evict(connection)
            connection.commit()

    def get(self, entry_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, agent, source, destination, kind, state, text, created_at, updated_at "
                "FROM activity WHERE id = ?",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, agent, source, destination, kind, state, text, created_at, updated_at "
                "FROM activity ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def delete(self, entry_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM activity WHERE id = ?", (entry_id,))
            connection.commit()

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        keys = ("id", "agent", "source", "destination", "kind", "state", "text", "created_at", "updated_at")
        entry = dict(zip(keys, row))
        if not entry["source"]:
            entry.pop("source")
        if not entry["destination"]:
            entry.pop("destination")
        return entry
