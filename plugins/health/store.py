"""Small durable cache for Vitalist reports; Perseus remains the source of truth."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from orion.core.config import config

_DB = config.root() / "data" / "vitalist.db"
_READY = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  facts TEXT NOT NULL,
  prose TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(scope, period_end));
CREATE INDEX IF NOT EXISTS vitalist_reports_scope ON reports(scope, id DESC);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _READY
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 30000")
    c.execute("PRAGMA journal_mode = WAL")
    if not _READY:
        c.executescript(_SCHEMA)
        _READY = True
    return c


def save(c: sqlite3.Connection, facts: dict[str, Any], prose: dict[str, Any]) -> int:
    c.execute(
        "INSERT INTO reports(scope,period_start,period_end,facts,prose,created_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(scope,period_end) DO UPDATE SET "
        "period_start=excluded.period_start,facts=excluded.facts,prose=excluded.prose,"
        "created_at=excluded.created_at",
        (facts["scope"], facts["period_start"], facts["period_end"],
         json.dumps(facts), json.dumps(prose), now()))
    row = c.execute("SELECT id FROM reports WHERE scope=? AND period_end=?",
                    (facts["scope"], facts["period_end"])).fetchone()
    c.execute("DELETE FROM reports WHERE id NOT IN (SELECT id FROM reports ORDER BY id DESC LIMIT 120)")
    c.commit()
    return int(row["id"])


def latest(c: sqlite3.Connection, scope: str) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM reports WHERE scope=? ORDER BY id DESC LIMIT 1", (scope,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "scope": row["scope"], "period_start": row["period_start"],
            "period_end": row["period_end"], "facts": json.loads(row["facts"]),
            "prose": json.loads(row["prose"]), "created_at": row["created_at"]}


def set_meta(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute("INSERT INTO meta(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
              "value=excluded.value,updated_at=excluded.updated_at", (key, value, now()))
    c.commit()


def get_meta(c: sqlite3.Connection, key: str) -> str | None:
    row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None
