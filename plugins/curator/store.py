"""Curator's own SQLite store (``data/curator.db``) — the single data layer for every pass.

v1 kept just two tables here (``notes`` checkpoints + grammar ``proposals``). v2 grows the
store to back the whole editor: per-pass checkpoints so grammar / memory / backlink passes
each skip unchanged notes independently, a typed proposal queue (grammar · backlink ·
entity_note), a resident **entity registry** (the vault's cast of people/places/projects), and
a **gap-question** queue. Migration is additive only — ``CREATE TABLE IF NOT EXISTS`` +
``ADD COLUMN`` guarded by a probe — so an existing v1 db (with live proposals) upgrades in
place without losing a row.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from orion.core.config import config

_DB = config.root() / "data" / "curator.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    path TEXT PRIMARY KEY, sha TEXT NOT NULL, checked_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL, original_sha TEXT NOT NULL,
    corrected_text TEXT NOT NULL, diff TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL, resolved_at TEXT);

-- the vault's resident cast; approved rows mirror into the World Model as entities
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical TEXT NOT NULL UNIQUE,     -- normalized key ("naufil", "mira road")
    name TEXT NOT NULL,                 -- display name ("Naufil")
    type TEXT NOT NULL,                 -- person | place | project | org
    aliases TEXT NOT NULL DEFAULT '[]', -- JSON list of surface forms seen
    mentions INTEGER NOT NULL DEFAULT 0,
    note_path TEXT,                     -- hub note once authored (People/Naufil.md)
    wm_entity_id INTEGER,               -- World Model entity id once mirrored
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

-- gap-finding: things the model could not resolve, queued for the user to answer
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL, question TEXT NOT NULL,
    answer TEXT, status TEXT NOT NULL DEFAULT 'open',  -- open | answered | dismissed
    created_at TEXT NOT NULL, answered_at TEXT);
"""

# columns added after v1 shipped: (table, column, decl)
_MIGRATIONS = [
    ("proposals", "kind", "TEXT NOT NULL DEFAULT 'grammar'"),
    ("notes", "mined_sha", "TEXT"),     # memory-extraction checkpoint
    ("notes", "linked_sha", "TEXT"),    # backlink-pass checkpoint
    ("notes", "entity_sha", "TEXT"),    # entity-registry pass checkpoint
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    _migrate(c)
    return c


def _migrate(c: sqlite3.Connection) -> None:
    for table, col, decl in _MIGRATIONS:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    c.commit()


# -- checkpoints -----------------------------------------------------------
def checkpoints(c: sqlite3.Connection, column: str = "sha") -> dict[str, str]:
    """{path: sha} for one checkpoint column (sha | mined_sha | linked_sha)."""
    return {r["path"]: r[column] for r in c.execute(
        f"SELECT path, {column} FROM notes WHERE {column} IS NOT NULL")}


def mark_note(c: sqlite3.Connection, path: str, **columns: str) -> None:
    """Upsert one note's checkpoint column(s) without disturbing the others."""
    row = c.execute("SELECT path FROM notes WHERE path=?", (path,)).fetchone()
    ts = now()
    if row is None:
        c.execute("INSERT INTO notes (path, sha, checked_at, mined_sha, linked_sha, entity_sha) "
                  "VALUES (?,?,?,?,?,?)",
                  (path, columns.get("sha") or "", ts, columns.get("mined_sha"),
                   columns.get("linked_sha"), columns.get("entity_sha")))
    else:
        sets = ", ".join(f"{k}=?" for k in columns)
        c.execute(f"UPDATE notes SET {sets}, checked_at=? WHERE path=?",
                  (*columns.values(), ts, path))
    c.commit()


# -- proposals -------------------------------------------------------------
def add_proposal(c: sqlite3.Connection, path: str, original_sha: str, corrected_text: str,
                 diff: str, kind: str = "grammar") -> int:
    cur = c.execute(
        "INSERT INTO proposals (path, original_sha, corrected_text, diff, kind, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (path, original_sha, corrected_text, diff, kind, now()))
    c.commit()
    return cur.lastrowid


def proposals(c: sqlite3.Connection, status: str = "pending",
              kind: str | None = None) -> list[dict[str, Any]]:
    q = ("SELECT id, path, diff, status, kind, created_at FROM proposals "
         "WHERE status=?")
    params: list[Any] = [status]
    if kind:
        q += " AND kind=?"
        params.append(kind)
    q += " ORDER BY id DESC"
    return [dict(r) for r in c.execute(q, params)]


def pending_paths(c: sqlite3.Connection, kinds: list[str] | None = None) -> set[str]:
    """Paths with a pending proposal. Filter to specific kinds when a pass only needs to
    avoid colliding with edits of its own kind (entity/memory passes pass kinds=[] → none)."""
    if kinds is None:
        return {r["path"] for r in c.execute(
            "SELECT path FROM proposals WHERE status='pending'")}
    if not kinds:
        return set()
    ph = ",".join("?" * len(kinds))
    return {r["path"] for r in c.execute(
        f"SELECT path FROM proposals WHERE status='pending' AND kind IN ({ph})", tuple(kinds))}


def counts(c: sqlite3.Connection) -> dict[str, int]:
    out = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM proposals GROUP BY status")}
    out["notes_tracked"] = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    out["mined"] = c.execute(
        "SELECT COUNT(*) FROM notes WHERE mined_sha IS NOT NULL").fetchone()[0]
    out["entities"] = c.execute(
        "SELECT COUNT(*) FROM entities WHERE status='approved'").fetchone()[0]
    out["questions_open"] = c.execute(
        "SELECT COUNT(*) FROM questions WHERE status='open'").fetchone()[0]
    return out
