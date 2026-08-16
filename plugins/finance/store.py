"""Treasurer's evidence ledger (``data/treasurer.db``).

FinStrive remains the source of truth. This database contains derived snapshots, explainable
insights, model validation records, and explicit user feedback. Raw transaction descriptions
are deliberately not persisted here.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from orion.core.config import config

_DB = config.root() / "data" / "treasurer.db"
_BUSY_TIMEOUT_MS = 30_000
_READY = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of TEXT NOT NULL,
    source_latest TEXT,
    transaction_count INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS snapshots_created ON snapshots(id DESC);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence TEXT NOT NULL,
    llm_json TEXT,
    confidence REAL NOT NULL,
    severity TEXT NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    review_state TEXT NOT NULL DEFAULT 'pending',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    model_run_id INTEGER);
CREATE INDEX IF NOT EXISTS insights_state ON insights(status, review_state, id DESC);

CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    trained_through TEXT,
    method TEXT NOT NULL,
    samples INTEGER NOT NULL,
    metrics TEXT NOT NULL,
    created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _READY
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB, timeout=_BUSY_TIMEOUT_MS / 1000)
    c.row_factory = sqlite3.Row
    c.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA synchronous = NORMAL")
    if not _READY:
        c.executescript(_SCHEMA)
        _READY = True
    return c


def add_snapshot(c: sqlite3.Connection, payload: dict[str, Any]) -> int:
    cur = c.execute(
        "INSERT INTO snapshots (as_of,source_latest,transaction_count,payload,created_at) "
        "VALUES (?,?,?,?,?)",
        (payload["as_of"], payload.get("latest_transaction_at"),
         int(payload.get("transaction_count", 0)), json.dumps(payload), now()))
    snapshot_id = int(cur.lastrowid)
    c.execute("DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 800)")
    c.commit()
    return snapshot_id


def latest_snapshot(c: sqlite3.Connection) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    out = json.loads(row["payload"])
    out["snapshot_id"] = row["id"]
    out["cached_at"] = row["created_at"]
    return out


def add_model_run(c: sqlite3.Connection, *, version: str, trained_through: str | None,
                  method: str, samples: int, metrics: dict[str, Any]) -> int:
    cur = c.execute(
        "INSERT INTO model_runs (version,trained_through,method,samples,metrics,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (version, trained_through, method, samples, json.dumps(metrics), now()))
    model_id = int(cur.lastrowid)
    c.execute("DELETE FROM model_runs WHERE id NOT IN (SELECT id FROM model_runs ORDER BY id DESC LIMIT 200)")
    c.commit()
    return model_id


def latest_model_run(c: sqlite3.Connection) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM model_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    out = dict(row)
    out["metrics"] = json.loads(out["metrics"])
    return out


def upsert_insights(c: sqlite3.Connection, found: list[dict[str, Any]],
                    model_run_id: int | None = None) -> list[int]:
    """Refresh current findings and resolve previously-active findings that disappeared."""
    ts = now()
    seen: list[str] = []
    ids: list[int] = []
    for item in found:
        fp = str(item["fingerprint"])
        seen.append(fp)
        existing = c.execute("SELECT id,review_state FROM insights WHERE fingerprint=?", (fp,)).fetchone()
        values = (
            item["kind"], item.get("scope", "overall"), item["title"], item["detail"],
            json.dumps(item.get("evidence", {})),
            json.dumps(item.get("llm")) if item.get("llm") else None,
            float(item.get("confidence", 0.5)), item.get("severity", "notice"),
            item.get("method", "rules"), ts, model_run_id, fp)
        if existing:
            c.execute(
                "UPDATE insights SET kind=?,scope=?,title=?,detail=?,evidence=?,"
                "llm_json=COALESCE(?,llm_json),"
                "confidence=?,severity=?,method=?,status='active',last_seen_at=?,resolved_at=NULL,"
                "model_run_id=? WHERE fingerprint=?", values)
            ids.append(int(existing["id"]))
        else:
            cur = c.execute(
                "INSERT INTO insights (kind,scope,title,detail,evidence,llm_json,confidence,"
                "severity,method,first_seen_at,last_seen_at,model_run_id,fingerprint) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values[:9] + (ts, ts, model_run_id, fp))
            ids.append(int(cur.lastrowid))

    if seen:
        marks = ",".join("?" for _ in seen)
        c.execute(
            f"UPDATE insights SET status='resolved',resolved_at=? WHERE status='active' "
            f"AND fingerprint NOT IN ({marks})", (ts, *seen))
    else:
        c.execute("UPDATE insights SET status='resolved',resolved_at=? WHERE status='active'", (ts,))
    c.commit()
    return ids


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["evidence"] = json.loads(out.get("evidence") or "{}")
    out["llm"] = json.loads(out["llm_json"]) if out.get("llm_json") else None
    out.pop("llm_json", None)
    return out


def insights(c: sqlite3.Connection, *, status: str | None = "active",
             review_state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if review_state:
        clauses.append("review_state=?")
        params.append(review_state)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = c.execute(f"SELECT * FROM insights{where} ORDER BY confidence DESC,id DESC LIMIT ?", params)
    return [_decode(r) for r in rows]


def insight(c: sqlite3.Connection, iid: int) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM insights WHERE id=?", (iid,)).fetchone()
    return _decode(row) if row else None


def review(c: sqlite3.Connection, iid: int, state: str) -> bool:
    if state not in {"useful", "expected", "dismissed", "remembered"}:
        return False
    cur = c.execute("UPDATE insights SET review_state=? WHERE id=?", (state, iid))
    c.commit()
    return cur.rowcount > 0


def set_meta(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute(
        "INSERT INTO meta (key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
        "SET value=excluded.value,updated_at=excluded.updated_at", (key, value, now()))
    c.commit()


def get_meta(c: sqlite3.Connection, key: str) -> str | None:
    row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def counts(c: sqlite3.Connection) -> dict[str, int]:
    return {
        "snapshots": c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
        "active": c.execute("SELECT COUNT(*) FROM insights WHERE status='active'").fetchone()[0],
        "pending": c.execute("SELECT COUNT(*) FROM insights WHERE status='active' AND review_state='pending'").fetchone()[0],
    }
