"""Maintainer's own SQLite store (``data/maintainer.db``) — the work queue and the run log.

Three tables and one rule: **a task is a durable row before it is a Codex run**, the same
discipline Herald applies to mail. That is what lets a brief wait in your inbox indefinitely, a
killed runner be reaped instead of stranding work, and every run be readable afterwards —
what was asked, what changed, what it cost, whether the build passed.

  tasks   the queue. proposed → approved → claimed → running → done | failed (or rejected)
  runs    one attempt at a task: branch, PR, diffstat, verification, Codex accounting
  events  the runner's progress feed, so the agent page shows what Codex is doing live

Same connection discipline as the Curator's and Herald's stores — WAL plus a 30s
``busy_timeout`` — because the scan pass, the sweep pass and the runner's HTTP callbacks will
overlap sooner or later, and the gate only holds background work back from *chat*.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from orion.core.config import config

_DB = config.root() / "data" / "maintainer.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    title TEXT NOT NULL,
    slug TEXT NOT NULL,                      -- branch-safe, derived from the title
    brief TEXT NOT NULL,                     -- what the runner hands Codex
    rationale TEXT NOT NULL DEFAULT '',      -- why this is worth a run, in the user's terms
    acceptance TEXT NOT NULL DEFAULT '',     -- how we will know it worked
    files TEXT NOT NULL DEFAULT '[]',        -- JSON list of likely-relevant paths
    risk TEXT NOT NULL DEFAULT 'low',        -- low | medium | high
    source TEXT NOT NULL DEFAULT 'scan',     -- scan | failure | manual
    -- proposed: waiting on you · approved: waiting on the runner · claimed/running: in flight
    -- done · failed · rejected · expired
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL,
    approved_at TEXT,
    claimed_at TEXT,
    finished_at TEXT,
    runner TEXT);

CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, id DESC);
CREATE INDEX IF NOT EXISTS tasks_repo ON tasks(repo, status);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running | done | failed
    branch TEXT,
    pr_url TEXT,
    pr_state TEXT,                           -- open | merged | closed
    commit_sha TEXT,
    files_changed INTEGER NOT NULL DEFAULT 0,
    insertions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    turns INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    duration_s REAL NOT NULL DEFAULT 0.0,
    verify TEXT NOT NULL DEFAULT 'skipped',  -- passed | failed | blocked | skipped
    verify_tail TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',        -- Codex's own account of what it did
    error TEXT,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    ended_at TEXT);

CREATE INDEX IF NOT EXISTS runs_task ON runs(task_id, id DESC);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status, id DESC);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,                      -- step | tool | text | error
    text TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);

-- small facts about the system itself: chiefly when the host runner last spoke to us, which
-- is the difference between "nothing to do" and "nobody is listening"
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    at TEXT NOT NULL);

-- HEAD at the last scan, so a repo with no new commits costs nothing to re-scan
CREATE TABLE IF NOT EXISTS scanned (
    repo TEXT PRIMARY KEY,
    head_sha TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    candidates INTEGER NOT NULL DEFAULT 0);
"""

_BUSY_TIMEOUT_MS = 30_000
_READY = False

OPEN_STATUSES = ("proposed", "approved", "claimed", "running")


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
        _migrate(c)
        _READY = True
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """Heal verification results that older runners mislabeled as build failures."""
    c.execute(
        "UPDATE runs SET verify='blocked' WHERE verify='failed' AND "
        "(verify_tail LIKE '%No SDKs were found%' "
        "OR verify_tail LIKE '%sh: line 1: next: not found%')")
    c.commit()


def slugify(title: str) -> str:
    """A branch-safe stem. Never empty — a blank slug would collide forever on the remote."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-")
    return s or "task"


# -- tasks -----------------------------------------------------------------
def add_task(c: sqlite3.Connection, repo: str, title: str, brief: str, *,
             rationale: str = "", acceptance: str = "", files: str = "[]",
             risk: str = "low", source: str = "scan") -> int:
    cur = c.execute(
        "INSERT INTO tasks (repo, title, slug, brief, rationale, acceptance, files, risk, "
        "source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (repo, title, slugify(title), brief, rationale, acceptance, files, risk, source, now()))
    c.commit()
    return cur.lastrowid


def get_task(c: sqlite3.Connection, tid: int) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    return dict(row) if row else None


def tasks(c: sqlite3.Connection, status: str | None = None,
          limit: int = 50) -> list[dict[str, Any]]:
    q = "SELECT * FROM tasks"
    params: list[Any] = []
    if status:
        q += " WHERE status=?"
        params.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in c.execute(q, params)]


def proposed(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """What is waiting on the user — the inbox's share."""
    return [dict(r) for r in c.execute(
        "SELECT * FROM tasks WHERE status='proposed' ORDER BY id DESC")]


def open_titles(c: sqlite3.Connection, repo: str) -> list[str]:
    """Titles already in flight for this repo, so a nightly scan cannot re-propose them."""
    marks = ",".join("?" * len(OPEN_STATUSES))
    return [r["title"] for r in c.execute(
        f"SELECT title FROM tasks WHERE repo=? AND status IN ({marks})", (repo, *OPEN_STATUSES))]


def recent_titles(c: sqlite3.Connection, repo: str, days: int = 90,
                  limit: int = 30) -> list[str]:
    """Recently considered work, including completed/rejected tasks.

    Nightly audits revisit unchanged code, so open-task deduplication alone is insufficient:
    the model can otherwise rediscover yesterday's rejected cleanup under the same title.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    return [r["title"] for r in c.execute(
        "SELECT title FROM tasks WHERE repo=? AND created_at>=? ORDER BY id DESC LIMIT ?",
        (repo, cutoff, limit))]


def set_status(c: sqlite3.Connection, tid: int, status: str, **stamps: Any) -> None:
    cols = ", ".join(f"{k}=?" for k in stamps)
    sql = f"UPDATE tasks SET status=?{', ' + cols if cols else ''} WHERE id=?"
    c.execute(sql, (status, *stamps.values(), tid))
    c.commit()


def approve(c: sqlite3.Connection, tid: int) -> None:
    set_status(c, tid, "approved", approved_at=now())


def claim_next(c: sqlite3.Connection, runner: str) -> dict[str, Any] | None:
    """Hand the oldest approved task to a runner, atomically.

    The UPDATE ... WHERE status='approved' is the lock: two runners racing means the second
    one changes zero rows and gets nothing, rather than both running the same task.
    """
    row = c.execute("SELECT * FROM tasks WHERE status='approved' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    cur = c.execute("UPDATE tasks SET status='claimed', claimed_at=?, runner=? "
                    "WHERE id=? AND status='approved'", (now(), runner, row["id"]))
    c.commit()
    if cur.rowcount == 0:
        return None
    return dict(c.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())


def expire_stale_proposals(c: sqlite3.Connection, days: int) -> int:
    """A brief you never acted on stops being news. Silently expiring beats a stale queue."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    cur = c.execute("UPDATE tasks SET status='expired', finished_at=? "
                    "WHERE status='proposed' AND created_at < ?", (now(), cutoff))
    c.commit()
    return cur.rowcount


# -- runs ------------------------------------------------------------------
def start_run(c: sqlite3.Connection, task_id: int, branch: str = "") -> int:
    cur = c.execute("INSERT INTO runs (task_id, branch, started_at, heartbeat_at) "
                    "VALUES (?,?,?,?)", (task_id, branch, now(), now()))
    c.commit()
    return cur.lastrowid


def heartbeat(c: sqlite3.Connection, run_id: int) -> None:
    c.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (now(), run_id))
    c.commit()


def finish_run(c: sqlite3.Connection, run_id: int, ok: bool, fields: dict[str, Any]) -> None:
    allowed = ("branch", "pr_url", "pr_state", "commit_sha", "files_changed", "insertions",
               "deletions", "turns", "cost_usd", "duration_s", "verify", "verify_tail",
               "summary", "error")
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    cols = "".join(f", {k}=?" for k in patch)
    c.execute(f"UPDATE runs SET status=?, ended_at=?, heartbeat_at=?{cols} WHERE id=?",
              ("done" if ok else "failed", now(), now(), *patch.values(), run_id))
    c.commit()


def get_run(c: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def runs(c: sqlite3.Connection, limit: int = 25, status: str | None = None,
         since_hours: float | None = None) -> list[dict[str, Any]]:
    """The run log, newest first, each row carrying its task's repo and title."""
    q = ("SELECT r.*, t.repo, t.title, t.risk FROM runs r JOIN tasks t ON t.id = r.task_id")
    where, params = [], []
    if status:
        where.append("r.status=?")
        params.append(status)
    if since_hours is not None:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=since_hours)).isoformat(timespec="seconds")
        where.append("r.started_at >= ?")
        params.append(cutoff)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in c.execute(q, params)]


def open_prs(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """Pull requests still waiting on you. This is the number the whole feature exists for."""
    return [dict(r) for r in c.execute(
        "SELECT r.*, t.repo, t.title FROM runs r JOIN tasks t ON t.id = r.task_id "
        "WHERE r.pr_url IS NOT NULL AND r.pr_url != '' "
        "AND (r.pr_state IS NULL OR r.pr_state='open') ORDER BY r.id DESC")]


def set_pr_state(c: sqlite3.Connection, pr_url: str, state: str) -> None:
    c.execute("UPDATE runs SET pr_state=? WHERE pr_url=?", (state, pr_url))
    c.commit()


def stale_runs(c: sqlite3.Connection, minutes: float) -> list[dict[str, Any]]:
    """Runs whose runner has gone quiet — a killed process, a rebooted host, a hung build."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    return [dict(r) for r in c.execute(
        "SELECT * FROM runs WHERE status='running' AND heartbeat_at < ?", (cutoff,))]


# -- events ----------------------------------------------------------------
def add_events(c: sqlite3.Connection, run_id: int, items: list[dict[str, Any]]) -> int:
    rows = [(run_id, str(i.get("at") or now()), str(i.get("kind") or "text"),
             str(i.get("text") or "")[:1000]) for i in items]
    if not rows:
        return 0
    c.executemany("INSERT INTO events (run_id, at, kind, text) VALUES (?,?,?,?)", rows)
    c.commit()
    return len(rows)


def events(c: sqlite3.Connection, run_id: int, limit: int = 200) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(
        "SELECT at, kind, text FROM events WHERE run_id=? ORDER BY id LIMIT ?", (run_id, limit))]


# -- scan checkpoints ------------------------------------------------------
def last_scan(c: sqlite3.Connection, repo: str) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM scanned WHERE repo=?", (repo,)).fetchone()
    return dict(row) if row else None


def mark_scanned(c: sqlite3.Connection, repo: str, head_sha: str, candidates: int) -> None:
    c.execute("INSERT INTO scanned (repo, head_sha, scanned_at, candidates) VALUES (?,?,?,?) "
              "ON CONFLICT(repo) DO UPDATE SET head_sha=excluded.head_sha, "
              "scanned_at=excluded.scanned_at, candidates=excluded.candidates",
              (repo, head_sha, now(), candidates))
    c.commit()


# -- meta ------------------------------------------------------------------
def meta_set(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute("INSERT INTO meta (key, value, at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
              "SET value=excluded.value, at=excluded.at", (key, value[:200], now()))
    c.commit()


def meta_get(c: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = c.execute("SELECT value, at FROM meta WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


def counts(c: sqlite3.Connection) -> dict[str, int]:
    out = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
    out["open_prs"] = len(open_prs(c))
    out["runs"] = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    return out
