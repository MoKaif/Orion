"""World Model SQLite schema (DDL).

One small SQLite file on an 8GB CPU-only box. Entity/relationship *types* are free-text so
plugins can register new ones without migrations. The ``vectors`` virtual table is created
separately by vectors.py (needs the sqlite-vec extension loaded).
"""

# Knowledge kinds and lifecycle status — mirrored from the constitution.
KINDS = ("fact", "observation", "idea")
STATUSES = ("pending", "accepted", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY,
    type          TEXT NOT NULL,            -- plugin-extensible (person, project, concept, workspace, ...)
    name          TEXT NOT NULL,
    canonical_key TEXT UNIQUE,              -- for dedupe/merge
    source        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge (
    id          INTEGER PRIMARY KEY,
    entity_id   INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('fact','observation','idea')),
    confidence  REAL NOT NULL DEFAULT 1.0,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    source      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS relationships (
    id         INTEGER PRIMARY KEY,
    src_id     INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    dst_id     INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,               -- plugin-extensible (originated_from, depends_on, improves, ...)
    confidence REAL NOT NULL DEFAULT 1.0,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    source     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The timeline: "what happened, when" (hybrid memory alongside the semantic graph).
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    type       TEXT NOT NULL,
    payload    TEXT,                        -- JSON
    entity_id  INTEGER REFERENCES entities(id) ON DELETE SET NULL,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A workspace is a bounded area of work (project = trip = learning goal). It IS an entity;
-- this table holds workspace-specific fields.
CREATE TABLE IF NOT EXISTS workspaces (
    entity_id INTEGER PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
    goal      TEXT,
    status    TEXT DEFAULT 'active'
);

-- The lifecycle gate: nothing inferred becomes truth until Accept/Edit/Reject.
CREATE TABLE IF NOT EXISTS review_inbox (
    id         INTEGER PRIMARY KEY,
    item_type  TEXT NOT NULL,               -- knowledge | relationship | discovery | duplicate
    payload    TEXT NOT NULL,               -- JSON describing the proposed change
    confidence REAL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    title      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,               -- user | assistant | system | tool
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_entity  ON knowledge(entity_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_status  ON knowledge(status);
CREATE INDEX IF NOT EXISTS idx_rel_src           ON relationships(src_id);
CREATE INDEX IF NOT EXISTS idx_rel_dst           ON relationships(dst_id);
CREATE INDEX IF NOT EXISTS idx_review_status     ON review_inbox(status);
CREATE INDEX IF NOT EXISTS idx_messages_session  ON messages(session_id);
"""
