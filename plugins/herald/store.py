"""Herald's own SQLite store (``data/herald.db``) — the outbox and the alert ledger.

Mail is the one thing Orion does that leaves the machine and cannot be undone, so every message
is a durable row *before* it is a network call. That buys three things a fire-and-forget
``smtplib`` call cannot: an approval gate can hold a message indefinitely, a failed send can be
retried without recomposing it, and the user can always read exactly what was sent in their name.

``alerts`` is a cooldown ledger, not a queue: it exists so a job that has been failing since
Tuesday produces one mail, not one every half hour.

Same connection discipline as the Curator's store — WAL plus a 30s ``busy_timeout`` — because
Herald's watcher runs every half hour and will overlap the nightly passes sooner or later.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from orion.core.config import config

_DB = config.root() / "data" / "herald.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                      -- briefing | weekly | alert | nudge | manual
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    html TEXT NOT NULL,
    text TEXT NOT NULL,
    -- queued: waiting on the sender · held: waiting on the user (non-self recipient)
    -- sent · failed · cancelled
    status TEXT NOT NULL DEFAULT 'queued',
    reason TEXT,                             -- why it is held, or why it failed
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    sent_at TEXT);

CREATE INDEX IF NOT EXISTS outbox_status ON outbox(status, id DESC);

-- when each alert key last went out, so a standing problem mails once, not hourly
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY,
    last_sent_at TEXT NOT NULL,
    detail TEXT);
"""

_BUSY_TIMEOUT_MS = 30_000
_READY = False


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    """A connection tuned the way the Curator's is: WAL, and a wait long enough to outlast
    another job holding the write lock."""
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


# -- outbox ----------------------------------------------------------------
def queue(c: sqlite3.Connection, kind: str, to_addr: str, subject: str,
          html: str, text: str, status: str = "queued", reason: str | None = None) -> int:
    cur = c.execute(
        "INSERT INTO outbox (kind, to_addr, subject, html, text, status, reason, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (kind, to_addr, subject, html, text, status, reason, now()))
    c.commit()
    return cur.lastrowid


def mark_sent(c: sqlite3.Connection, mid: int) -> None:
    c.execute("UPDATE outbox SET status='sent', sent_at=?, reason=NULL, "
              "attempts=attempts+1 WHERE id=?", (now(), mid))
    c.commit()


def mark_failed(c: sqlite3.Connection, mid: int, error: str) -> None:
    c.execute("UPDATE outbox SET status='failed', reason=?, attempts=attempts+1 WHERE id=?",
              (error[:400], mid))
    c.commit()


def set_status(c: sqlite3.Connection, mid: int, status: str, reason: str | None = None) -> None:
    c.execute("UPDATE outbox SET status=?, reason=? WHERE id=?", (status, reason, mid))
    c.commit()


def get(c: sqlite3.Connection, mid: int) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM outbox WHERE id=?", (mid,)).fetchone()
    return dict(row) if row else None


def recent(c: sqlite3.Connection, limit: int = 25,
           status: str | None = None) -> list[dict[str, Any]]:
    """The mail log, newest first. Bodies are omitted — this feeds a list view."""
    q = ("SELECT id, kind, to_addr, subject, status, reason, attempts, created_at, sent_at "
         "FROM outbox")
    params: list[Any] = []
    if status:
        q += " WHERE status=?"
        params.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in c.execute(q, params)]


def held(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """Messages waiting on the user's approval (anything addressed outside the account)."""
    return [dict(r) for r in c.execute(
        "SELECT id, kind, to_addr, subject, text, reason, created_at FROM outbox "
        "WHERE status='held' ORDER BY id DESC")]


def sent_since(c: sqlite3.Connection, hours: int = 24) -> int:
    """How many messages actually went out recently — the input to the daily cap."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return c.execute("SELECT COUNT(*) FROM outbox WHERE status='sent' AND sent_at >= ?",
                     (cutoff,)).fetchone()[0]


def counts(c: sqlite3.Connection) -> dict[str, int]:
    out = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM outbox GROUP BY status")}
    out["sent_24h"] = sent_since(c, 24)
    return out


def last_sent(c: sqlite3.Connection) -> dict[str, Any] | None:
    row = c.execute("SELECT id, kind, subject, sent_at FROM outbox WHERE status='sent' "
                    "ORDER BY sent_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# -- alert cooldowns -------------------------------------------------------
def alert_due(c: sqlite3.Connection, key: str, cooldown_hours: float) -> bool:
    """True if this alert key has not fired inside its cooldown window.

    Keys carry the *identity* of the problem (``job_failed:curate_vault``), not the moment it
    was noticed, so a job failing all night is one mail rather than forty-eight.
    """
    row = c.execute("SELECT last_sent_at FROM alerts WHERE key=?", (key,)).fetchone()
    if row is None:
        return True
    try:
        last = datetime.fromisoformat(row["last_sent_at"])
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(hours=cooldown_hours)


def mark_alerted(c: sqlite3.Connection, key: str, detail: str = "") -> None:
    c.execute("INSERT INTO alerts (key, last_sent_at, detail) VALUES (?,?,?) "
              "ON CONFLICT(key) DO UPDATE SET last_sent_at=excluded.last_sent_at, "
              "detail=excluded.detail", (key, now(), detail[:400]))
    c.commit()


def clear_alert(c: sqlite3.Connection, key: str) -> None:
    """Forget a key so the next occurrence alerts immediately (a job that went green again)."""
    c.execute("DELETE FROM alerts WHERE key=?", (key,))
    c.commit()
