"""Persistent project identity and explicit configuration, separate from observations.

The legacy manifest seeds records once. Subsequent observations never overwrite owner
settings. Discovery walks only the configured workspace, with bounded depth and exclusions.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from . import repos, store

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    settings TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    observed_at TEXT NOT NULL,
    revision TEXT,
    fingerprint TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS project_observations_latest
    ON project_observations(project_id, id DESC);
CREATE TABLE IF NOT EXISTS project_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_remote_observations (
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    attempted_at TEXT NOT NULL,
    verified_at TEXT,
    origin TEXT,
    refs TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
"""

EXCLUDED = {".git", "node_modules", "data", "dist", "build", "bin", "obj", "target",
            "venv", ".venv", "vendor", "archive", "archives", "backups", "worktrees"}


def conn() -> sqlite3.Connection:
    c = store.conn()
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(SCHEMA)
    return c


def confined(path: str | Path) -> Path:
    workspace = repos.workspace().expanduser().resolve()
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = workspace / root
    root = root.resolve()
    if root == workspace or workspace not in root.parents:
        raise ValueError("Project path must be inside the configured workspace")
    return root


def discover() -> list[Path]:
    """No symlink traversal, home-directory scan, Git commands, or repository mutations."""
    workspace = repos.workspace().expanduser().resolve()
    found: list[Path] = []
    visited = 0
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        visited += 1
        if visited > 1000 or len(found) >= 100:
            break
        root = Path(directory)
        depth = len(root.relative_to(workspace).parts)
        if root != workspace and (".git" in dirs or ".git" in files):
            found.append(confined(root))
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if depth < 3 and not d.startswith(".")
                         and d.lower() not in EXCLUDED and not (root / d).is_symlink())
    return found


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["settings"] = json.loads(result["settings"])
    return result


def projects(c: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_decode(r) for r in c.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")]


def get(c: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return _decode(row) if row else None


def register(c: sqlite3.Connection, name: str, path: str | Path, *,
             settings: dict | None = None, source: str = "configured") -> str:
    root = confined(path)
    row = c.execute("SELECT id FROM projects WHERE path=?", (str(root),)).fetchone()
    if row:
        return row["id"]
    # The random ID survives future path/name changes and is not derived from a checkout.
    project_id = str(uuid.uuid4())
    stamp = store.now()
    if c.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
        name = str(root.relative_to(repos.workspace().expanduser().resolve()))
    c.execute("INSERT INTO projects(id,name,path,settings,source,created_at,updated_at) "
              "VALUES(?,?,?,?,?,?,?)", (project_id, name, str(root),
              json.dumps(settings or {}), source, stamp, stamp))
    c.execute("INSERT INTO project_activity(project_id,at,kind,detail) VALUES(?,?,?,?)",
              (project_id, stamp, "registered", source))
    c.commit()
    return project_id


def onboard(c: sqlite3.Connection, *, discovery: bool = False) -> int:
    before = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    for name, cfg in (repos.settings().get("repos") or {}).items():
        if name.startswith("//") or not isinstance(cfg, dict):
            continue
        settings = {k: v for k, v in cfg.items() if not k.startswith("//") and k != "path"}
        # Manifest import is explicit configuration, not an inferred release policy.
        register(c, name, cfg.get("path", name), settings=settings, source="manifest")
    if discovery:
        for root in discover():
            register(c, root.name, root, source="workspace_discovery")
    return c.execute("SELECT COUNT(*) FROM projects").fetchone()[0] - before


def update(c: sqlite3.Connection, project_id: str, revision: int, changes: dict) -> dict:
    current = get(c, project_id)
    if current is None:
        raise KeyError(project_id)
    if current["revision"] != revision:
        raise ValueError("Configuration changed; reload before saving")
    settings = {**current["settings"], **changes.get("settings", {})}
    status = changes.get("status", current["status"])
    if status not in {"active", "paused", "archived"}:
        raise ValueError("Invalid project status")
    path = str(confined(changes.get("path", current["path"])))
    cur = c.execute("UPDATE projects SET settings=?, status=?, path=?, revision=revision+1, "
                    "updated_at=? WHERE id=? AND revision=?",
                    (json.dumps(settings), status, path, store.now(), project_id, revision))
    if not cur.rowcount:
        raise ValueError("Configuration changed; reload before saving")
    c.execute("INSERT INTO project_activity(project_id,at,kind,detail) VALUES(?,?,?,?)",
              (project_id, store.now(), "configuration_updated", f"revision {revision + 1}"))
    c.commit()
    return get(c, project_id)


def latest(c: sqlite3.Connection, project_id: str) -> dict | None:
    row = c.execute("SELECT * FROM project_observations WHERE project_id=? ORDER BY id DESC LIMIT 1",
                    (project_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "payload": json.loads(row["payload"])}


def save_observation(c: sqlite3.Connection, project_id: str, payload: dict) -> None:
    import hashlib
    serialized = json.dumps(payload, sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    stamp = store.now()
    c.execute("INSERT INTO project_observations(project_id,observed_at,revision,fingerprint,payload) "
              "VALUES(?,?,?,?,?)", (project_id, stamp, payload.get("git", {}).get("head"),
                                      fingerprint, serialized))
    # Bounded history; explicit project settings and legacy execution history are retained.
    c.execute("DELETE FROM project_observations WHERE project_id=? AND id NOT IN "
              "(SELECT id FROM project_observations WHERE project_id=? ORDER BY id DESC LIMIT 144)",
              (project_id, project_id))
    c.commit()
