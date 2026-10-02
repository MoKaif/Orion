"""Durable Chronos cache. Raw mail is deliberately never stored."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from orion.core.config import config

_DB = config.root() / "data" / "chronos.db"
_READY = False
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  google_id TEXT PRIMARY KEY, summary TEXT NOT NULL, start_at TEXT NOT NULL,
  end_at TEXT, location TEXT, html_link TEXT, updated_at TEXT, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chronos_events_start ON events(start_at);
CREATE TABLE IF NOT EXISTS mail_seen (
  fingerprint TEXT PRIMARY KEY, processed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_kind TEXT NOT NULL, source_ref TEXT NOT NULL,
  title TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT, location TEXT,
  description TEXT, confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  calendar_event_id TEXT, created_at TEXT NOT NULL, resolved_at TEXT,
  UNIQUE(source_kind, source_ref, title, start_at));
CREATE INDEX IF NOT EXISTS chronos_proposals_status ON proposals(status, id DESC);
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
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA journal_mode=WAL")
    if not _READY:
        c.executescript(_SCHEMA)
        _READY = True
    return c


def replace_events(c: sqlite3.Connection, events: list[dict[str, Any]]) -> None:
    ids = []
    for event in events:
        ids.append(event["google_id"])
        c.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(google_id) DO UPDATE SET "
            "summary=excluded.summary,start_at=excluded.start_at,end_at=excluded.end_at,"
            "location=excluded.location,html_link=excluded.html_link,updated_at=excluded.updated_at,"
            "payload=excluded.payload",
            (event["google_id"], event["summary"], event["start_at"], event.get("end_at"),
             event.get("location"), event.get("html_link"), event.get("updated_at"),
             json.dumps(event.get("payload") or {})))
    if ids:
        marks = ",".join("?" for _ in ids)
        c.execute(f"DELETE FROM events WHERE google_id NOT IN ({marks})", ids)
    else:
        c.execute("DELETE FROM events")
    c.commit()


def upcoming(c: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    today = datetime.now().astimezone().date().isoformat()
    rows = c.execute("SELECT * FROM events WHERE substr(start_at,1,10)>=? ORDER BY start_at LIMIT ?",
                     (today, limit)).fetchall()
    return [dict(row) for row in rows]


def seen(c: sqlite3.Connection, fingerprint: str) -> bool:
    return c.execute("SELECT 1 FROM mail_seen WHERE fingerprint=?", (fingerprint,)).fetchone() is not None


def mark_seen(c: sqlite3.Connection, fingerprint: str) -> None:
    c.execute("INSERT OR IGNORE INTO mail_seen VALUES(?,?)", (fingerprint, now()))
    c.commit()


def add_proposal(c: sqlite3.Connection, item: dict[str, Any]) -> int | None:
    cur = c.execute(
        "INSERT OR IGNORE INTO proposals(source_kind,source_ref,title,start_at,end_at,location,"
        "description,confidence,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (item["source_kind"], item["source_ref"], item["title"], item["start_at"],
         item.get("end_at"), item.get("location"), item.get("description"),
         item["confidence"], now()))
    c.commit()
    return int(cur.lastrowid) if cur.rowcount else None


def proposals(c: sqlite3.Connection, status: str = "pending", limit: int = 30) -> list[dict[str, Any]]:
    rows = c.execute("SELECT * FROM proposals WHERE status=? ORDER BY start_at,id DESC LIMIT ?",
                     (status, limit)).fetchall()
    return [dict(row) for row in rows]


def proposal(c: sqlite3.Connection, pid: int) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def resolve(c: sqlite3.Connection, pid: int, status: str, event_id: str | None = None) -> None:
    c.execute("UPDATE proposals SET status=?,calendar_event_id=?,resolved_at=? WHERE id=?",
              (status, event_id, now(), pid))
    c.commit()


def set_meta(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute("INSERT INTO meta VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
              "value=excluded.value,updated_at=excluded.updated_at", (key, value, now()))
    c.commit()


def get_meta(c: sqlite3.Connection, key: str) -> str | None:
    row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None
