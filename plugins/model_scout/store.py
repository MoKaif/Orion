"""Durable benchmark history and review-gated recommendations."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from orion.core.config import config

_DB = config.root() / "data" / "model_scout.db"
_READY = False
_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, current_model TEXT NOT NULL, results TEXT NOT NULL,
 research TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recommendations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
 candidate TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, effect TEXT NOT NULL,
 evidence TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
 resolved_at TEXT);
CREATE INDEX IF NOT EXISTS model_scout_rec_status ON recommendations(status,id DESC);
CREATE TABLE IF NOT EXISTS metadata (
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


def save_run(c, current_model: str, results: list[dict], research: list[dict], summary: str) -> int:
    cur = c.execute("INSERT INTO runs(current_model,results,research,summary,created_at) VALUES(?,?,?,?,?)",
                    (current_model, json.dumps(results), json.dumps(research), summary, now()))
    c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 52)")
    c.commit()
    return int(cur.lastrowid)


def _run(row) -> dict[str, Any]:
    item = dict(row)
    item["results"] = json.loads(item["results"])
    item["research"] = json.loads(item["research"])
    return item


def latest_run(c) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return _run(row) if row else None


def runs(c, limit: int = 20) -> list[dict[str, Any]]:
    return [_run(row) for row in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def set_metadata(c, key: str, value: Any) -> None:
    c.execute(
        "INSERT INTO metadata(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
        (key, json.dumps(value), now()),
    )
    c.commit()


def metadata(c, key: str) -> dict[str, Any] | None:
    row = c.execute("SELECT value,updated_at FROM metadata WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return {"value": json.loads(row["value"]), "updated_at": row["updated_at"]}


def propose(c, *, fingerprint: str, kind: str, candidate: str, title: str, body: str,
            effect: str, evidence: dict) -> int | None:
    cur = c.execute("INSERT OR IGNORE INTO recommendations(fingerprint,kind,candidate,title,body,"
                    "effect,evidence,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (fingerprint, kind, candidate, title, body, effect,
                     json.dumps(evidence), now()))
    c.commit()
    return int(cur.lastrowid) if cur.rowcount else None


def recommendations(c, status: str | None = "pending", limit: int = 30) -> list[dict[str, Any]]:
    where, args = ("WHERE status=?", [status]) if status else ("", [])
    rows = c.execute(f"SELECT * FROM recommendations {where} ORDER BY id DESC LIMIT ?",
                     (*args, limit)).fetchall()
    out = []
    for row in rows:
        item = dict(row); item["evidence"] = json.loads(item["evidence"]); out.append(item)
    return out


def recommendation(c, rid: int) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM recommendations WHERE id=?", (rid,)).fetchone()
    if not row: return None
    item = dict(row); item["evidence"] = json.loads(item["evidence"]); return item


def resolve(c, rid: int, status: str) -> None:
    c.execute("UPDATE recommendations SET status=?,resolved_at=? WHERE id=?", (status, now(), rid))
    c.commit()
