from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from .models import Goal


class SQLiteStorage:
    """Stores settings/state/goals and short-lived API cache only."""

    def __init__(self, path: Path | str, now: Callable[[], float] = time.time):
        self.path = Path(path)
        self.now = now

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version(version)
                    SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    CHECK (lower(key) NOT LIKE '%password%' AND lower(key) NOT LIKE '%token%' AND lower(key) NOT LIKE '%secret%')
                );
                CREATE TABLE IF NOT EXISTS allowed_identifiers (
                    kind TEXT PRIMARY KEY CHECK(kind IN ('user', 'chat')),
                    identifier INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_state (
                    source TEXT PRIMARY KEY,
                    cursor TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric TEXT NOT NULL,
                    target REAL NOT NULL CHECK(target > 0),
                    period TEXT NOT NULL CHECK(period IN ('daily','weekly','monthly')),
                    active INTEGER NOT NULL DEFAULT 1
                );
            """)

    def set_setting(self, key: str, value: str) -> None:
        if any(word in key.lower() for word in ("password", "token", "secret")):
            raise ValueError("Secrets cannot be stored in SQLite")
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

    def get_setting(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_allowed_identifiers(self, user_id: int, chat_id: int) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO allowed_identifiers(kind,identifier) VALUES(?,?)",
                (("user", user_id), ("chat", chat_id)),
            )

    def get_allowed_identifiers(self) -> tuple[int, int] | None:
        with self._connect() as db:
            rows = {row["kind"]: row["identifier"] for row in db.execute("SELECT * FROM allowed_identifiers")}
        return (rows["user"], rows["chat"]) if {"user", "chat"} <= rows.keys() else None

    def set_sync_state(self, source: str, cursor: str | None) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO sync_state(source,cursor,updated_at) VALUES(?,?,?)",
                (source, cursor, self.now()),
            )

    def get_sync_state(self, source: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT cursor FROM sync_state WHERE source=?", (source,)).fetchone()
        return row["cursor"] if row else None

    def cache_set(self, key: str, payload: dict, ttl_seconds: int = 3600) -> None:
        if not 1 <= ttl_seconds <= 86400:
            raise ValueError("Cache TTL must be between 1 second and 24 hours")
        self.cleanup_cache()
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO api_cache(cache_key,payload,expires_at) VALUES(?,?,?)",
                (key, json.dumps(payload), self.now() + ttl_seconds),
            )

    def cache_get(self, key: str) -> dict | None:
        self.cleanup_cache()
        with self._connect() as db:
            row = db.execute("SELECT payload FROM api_cache WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def cleanup_cache(self) -> int:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM api_cache WHERE expires_at <= ?", (self.now(),))
            return cursor.rowcount

    def create_goal(self, metric: str, target: float, period: str) -> Goal:
        if period not in {"daily", "weekly", "monthly"} or target <= 0:
            raise ValueError("Invalid goal")
        with self._connect() as db:
            cursor = db.execute("INSERT INTO goals(metric,target,period) VALUES(?,?,?)", (metric, target, period))
            goal_id = int(cursor.lastrowid or 0)
        return Goal(goal_id, metric, target, period)

    def list_goals(self, active_only: bool = False) -> list[Goal]:
        query = "SELECT id,metric,target,period,active FROM goals"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY id"
        with self._connect() as db:
            rows = db.execute(query).fetchall()
        return [Goal(r["id"], r["metric"], r["target"], r["period"], bool(r["active"])) for r in rows]

    def update_goal(self, goal_id: int, *, target: float | None = None, active: bool | None = None) -> Goal:
        with self._connect() as db:
            row = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not row:
                raise KeyError(goal_id)
            new_target = target if target is not None else row["target"]
            new_active = active if active is not None else bool(row["active"])
            if new_target <= 0:
                raise ValueError("Target must be positive")
            db.execute("UPDATE goals SET target=?,active=? WHERE id=?", (new_target, int(new_active), goal_id))
        return Goal(goal_id, row["metric"], new_target, row["period"], new_active)

    def delete_goal(self, goal_id: int) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM goals WHERE id=?", (goal_id,)).rowcount == 1
