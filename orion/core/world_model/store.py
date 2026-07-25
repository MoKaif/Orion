"""WorldModel — the interface every subsystem uses to read/write knowledge.

Implements the knowledge lifecycle (Observe -> Extract -> Infer -> Review -> commit) over
SQLite, plus hybrid recall (keyword + semantic when the local vector index is available).
Entity/relationship *types* are free-text so plugins extend the model without migrations.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from orion.core.config import config
from .schema import SCHEMA
from .vectors import vectors


class WorldModel:
    def __init__(self, db_path: Path | None = None):
        cfg = config.section("memory")
        self._path = db_path or (config.root() / cfg.get("db_path", "data/orion.db"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._kcfg = cfg.get("knowledge", {})
        self._init_schema()
        vectors.bind(self._path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- entities ----------------------------------------------------------
    def upsert_entity(self, type: str, name: str, canonical_key: str | None = None,
                      source: str | None = None) -> int:
        key = canonical_key or f"{type}:{name}".lower()
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM entities WHERE canonical_key=?", (key,)).fetchone()
            if row:
                conn.execute("UPDATE entities SET updated_at=datetime('now') WHERE id=?", (row["id"],))
                return row["id"]
            cur = conn.execute(
                "INSERT INTO entities (type,name,canonical_key,source) VALUES (?,?,?,?)",
                (type, name, key, source),
            )
            eid = cur.lastrowid
        vectors.add(f"e:{eid}", name)
        return eid

    def get_entity(self, entity_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        return dict(row) if row else None

    # -- knowledge (facts / observations / ideas) --------------------------
    def add_knowledge(self, entity_id: int, key: str, value: str, kind: str = "fact",
                      confidence: float = 1.0, status: str = "accepted",
                      source: str | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO knowledge (entity_id,key,value,kind,confidence,status,source)
                   VALUES (?,?,?,?,?,?,?)""",
                (entity_id, key, value, kind, confidence, status, source),
            )
            kid = cur.lastrowid
        if status == "accepted":
            vectors.add(f"k:{kid}", value)
        return kid

    def add_relationship(self, src_id: int, dst_id: int, type: str, confidence: float = 1.0,
                         status: str = "accepted", source: str | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO relationships (src_id,dst_id,type,confidence,status,source)
                   VALUES (?,?,?,?,?,?)""",
                (src_id, dst_id, type, confidence, status, source),
            )
            return cur.lastrowid

    def add_event(self, type: str, payload: dict[str, Any] | None = None,
                  entity_id: int | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO events (type,payload,entity_id) VALUES (?,?,?)",
                (type, json.dumps(payload or {}), entity_id),
            )
            return cur.lastrowid

    def create_workspace(self, name: str, goal: str | None = None) -> int:
        eid = self.upsert_entity("workspace", name)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workspaces (entity_id,goal,status) VALUES (?,?, 'active')",
                (eid, goal),
            )
        return eid

    # -- the review-inbox lifecycle gate -----------------------------------
    def ingest_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Apply the confidence policy to an extracted candidate.

        confidence >= auto_accept  -> committed straight to the world model.
        confidence <  review_floor -> discarded (too weak to be worth reviewing).
        otherwise                  -> queued in the review inbox for Accept/Edit/Reject.
        """
        conf = float(candidate.get("confidence", 0.5))
        auto = self._kcfg.get("auto_accept", 1.0)
        floor = self._kcfg.get("review_floor", 0.4)
        if conf < floor:
            return {"outcome": "discarded", "confidence": conf}
        if conf >= auto:
            self._commit_knowledge(candidate, status="accepted")
            return {"outcome": "accepted", "confidence": conf}
        rid = self.propose("knowledge", candidate, conf)
        return {"outcome": "queued", "review_id": rid, "confidence": conf}

    def propose(self, item_type: str, payload: dict[str, Any], confidence: float) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO review_inbox (item_type,payload,confidence) VALUES (?,?,?)",
                (item_type, json.dumps(payload), confidence),
            )
            return cur.lastrowid

    def pending_reviews(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_inbox WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def resolve_review(self, review_id: int, action: str,
                       edited_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Accept / edit / reject a queued item. Accept/edit commits it to the world model."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM review_inbox WHERE id=?", (review_id,)).fetchone()
            if not row:
                return {"error": "not found"}
            payload = edited_payload or json.loads(row["payload"])
            if action in ("accept", "edit"):
                if row["item_type"] == "knowledge":
                    self._commit_knowledge(payload, status="accepted")
                elif row["item_type"] == "relationship":
                    self.add_relationship(payload["src_id"], payload["dst_id"], payload["type"],
                                          payload.get("confidence", 1.0))
                new_status = "accepted"
            else:
                new_status = "rejected"
            conn.execute("UPDATE review_inbox SET status=?, payload=? WHERE id=?",
                         (new_status, json.dumps(payload), review_id))
        return {"outcome": new_status, "review_id": review_id}

    def _commit_knowledge(self, c: dict[str, Any], status: str) -> int:
        eid = self.upsert_entity(c.get("entity_type", "concept"), c["entity"],
                                 source=c.get("source"))
        return self.add_knowledge(eid, c.get("key", "note"), c["value"],
                                  kind=c.get("kind", "observation"),
                                  confidence=float(c.get("confidence", 1.0)),
                                  status=status, source=c.get("source"))

    # -- recall: the pipeline's context-assembly step ----------------------
    def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Hybrid recall: semantic KNN (when vectors available) merged with a keyword scan."""
        results: dict[int, dict[str, Any]] = {}

        # semantic
        if vectors.is_available():
            for hit in vectors.search(query, k=limit):
                if hit["ref"].startswith("k:"):
                    kid = int(hit["ref"][2:])
                    row = self._knowledge_row(kid)
                    if row:
                        row["score"] = hit.get("score", 0.0)
                        results[kid] = row

        # keyword: tokenize the query and match ANY significant term (the semantic index,
        # when available, handles fuzzier matches; this is the zero-dependency fallback).
        import re
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 3][:8]
        if terms:
            clause = " OR ".join(["(lower(k.value) LIKE ? OR lower(e.name) LIKE ?)"] * len(terms))
            params: list[Any] = []
            for t in terms:
                params += [f"%{t}%", f"%{t}%"]
            with self._connect() as conn:
                rows = conn.execute(
                    f"""SELECT k.id, e.name, e.type, k.key, k.value, k.kind, k.confidence
                        FROM knowledge k JOIN entities e ON e.id=k.entity_id
                        WHERE k.status='accepted' AND ({clause})
                        LIMIT ?""",
                    (*params, limit * 3),
                ).fetchall()
            for r in rows:
                d = dict(r)
                hay = f"{d['value']} {d['name']}".lower()
                d["score"] = sum(t in hay for t in terms) / len(terms)  # fraction of terms matched
                results.setdefault(d["id"], d)

        merged = sorted(results.values(), key=lambda d: (d.get("score", 0.0), d["confidence"]),
                        reverse=True)
        return merged[:limit]

    def _knowledge_row(self, kid: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT k.id, e.name, e.type, k.key, k.value, k.kind, k.confidence
                   FROM knowledge k JOIN entities e ON e.id=k.entity_id WHERE k.id=?""",
                (kid,),
            ).fetchone()
        return dict(row) if row else None

    # -- conversations -----------------------------------------------------
    def create_session(self, title: str | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO sessions (title) VALUES (?)", (title,))
            return cur.lastrowid

    def add_message(self, session_id: int, role: str, content: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id,role,content) VALUES (?,?,?)",
                (session_id, role, content),
            )
            return cur.lastrowid

    def history(self, session_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role,content,created_at FROM messages WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?", (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def sessions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    # -- introspection (used by background maintenance / briefings) --------
    def stats(self) -> dict[str, int]:
        counts = {}
        with self._connect() as conn:
            for table in ("entities", "knowledge", "relationships", "events", "sessions"):
                counts[table] = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
            counts["pending_reviews"] = conn.execute(
                "SELECT COUNT(*) c FROM review_inbox WHERE status='pending'").fetchone()["c"]
        return counts

    def type_counts(self) -> dict[str, int]:
        """{entity_type: count} — powers the graph legend and plugin domain widgets."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT type, COUNT(*) c FROM entities GROUP BY type ORDER BY c DESC"
            ).fetchall()
        return {r["type"]: r["c"] for r in rows}

    def entity_index(self) -> list[dict[str, Any]]:
        """(id, type, name) for every entity — for duplicate detection."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id, type, name FROM entities").fetchall()
        return [dict(r) for r in rows]

    def recent_messages(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # -- mission-control read models (M4) ----------------------------------
    def workspaces(self, status: str = "active", limit: int = 20) -> list[dict[str, Any]]:
        """Active bounded areas of work, newest-touched first, with a knowledge count."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.id, e.name, e.updated_at, w.goal, w.status,
                          (SELECT COUNT(*) FROM knowledge k
                           WHERE k.entity_id=e.id AND k.status='accepted') AS facts
                   FROM workspaces w JOIN entities e ON e.id=w.entity_id
                   WHERE w.status=? ORDER BY e.updated_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        """The activity feed — what Orion did / observed, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ev.type, ev.payload, ev.occurred_at, e.name AS entity
                   FROM events ev LEFT JOIN entities e ON e.id=ev.entity_id
                   ORDER BY ev.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out

    def recent_knowledge(self, limit: int = 12) -> list[dict[str, Any]]:
        """Most recently committed knowledge, for the dashboard's 'what Orion learned' strip."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT k.value, k.kind, k.confidence, k.created_at, e.name, e.type
                   FROM knowledge k JOIN entities e ON e.id=k.entity_id
                   WHERE k.status='accepted' ORDER BY k.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def graph_data(self, node_limit: int = 120) -> dict[str, list[dict[str, Any]]]:
        """Nodes (entities, sized by degree) + edges (accepted relationships) for the
        constellation view. Capped so the star map stays legible on the 8GB box."""
        with self._connect() as conn:
            rels = conn.execute(
                """SELECT r.src_id, r.dst_id, r.type, r.confidence
                   FROM relationships r WHERE r.status='accepted'"""
            ).fetchall()
            ents = conn.execute(
                "SELECT id, type, name FROM entities ORDER BY updated_at DESC LIMIT ?",
                (node_limit,),
            ).fetchall()
        keep = {e["id"] for e in ents}
        degree: dict[int, int] = {}
        edges = []
        for r in rels:
            if r["src_id"] in keep and r["dst_id"] in keep:
                edges.append({"source": r["src_id"], "target": r["dst_id"], "type": r["type"]})
                degree[r["src_id"]] = degree.get(r["src_id"], 0) + 1
                degree[r["dst_id"]] = degree.get(r["dst_id"], 0) + 1
        nodes = [{"id": e["id"], "name": e["name"], "type": e["type"],
                  "degree": degree.get(e["id"], 0)} for e in ents]
        return {"nodes": nodes, "edges": edges}


world_model = WorldModel()
