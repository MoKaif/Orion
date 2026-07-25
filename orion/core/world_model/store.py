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
        """Record a fact/observation/idea, **idempotently**.

        Re-stating the same (entity, key, value) updates that row instead of appending a new
        one. This used to append: the hourly vault index re-inserted every note's content each
        run, so one note accumulated a row per hour — 94% of the table was duplicates, and
        recall spent its slots on 30 copies of the same paragraph.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM knowledge WHERE entity_id=? AND key=? AND value=?",
                (entity_id, key, value),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE knowledge SET kind=?, confidence=?, status=?, source=? WHERE id=?",
                    (kind, confidence, status, source, row["id"]),
                )
                return row["id"]          # already embedded; nothing new to index
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
        effect: dict[str, Any] | None = None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM review_inbox WHERE id=?", (review_id,)).fetchone()
            if not row:
                return {"error": "not found"}
            payload = edited_payload or json.loads(row["payload"])
            item_type = row["item_type"]
            new_status = "accepted" if action in ("accept", "edit") else "rejected"
            conn.execute("UPDATE review_inbox SET status=?, payload=? WHERE id=?",
                         (new_status, json.dumps(payload), review_id))

        # committed outside the first transaction: these helpers open their own connections
        if new_status == "accepted":
            if item_type == "knowledge":
                self._commit_knowledge(payload, status="accepted")
            elif item_type == "relationship":
                self.add_relationship(payload["src_id"], payload["dst_id"], payload["type"],
                                      payload.get("confidence", 1.0))
            elif item_type == "duplicate":
                # recomputed here rather than trusted from the client: the card showed a plan,
                # accepting performs exactly that plan against the current state of the model.
                effect = self.apply_duplicate_plan(payload)
        out: dict[str, Any] = {"outcome": new_status, "review_id": review_id}
        if effect is not None:
            out["effect"] = effect
        return out

    # -- duplicates: say what will happen, then do exactly that ------------
    #: Vault paths whose notes are deleted or machine-owned — a copy indexed from here is stale.
    _STALE_DIRS = (".trash/", ".obsidian/")

    def duplicate_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """What accepting a duplicate notice would do, in enough detail to render on the card.

        Returns ``{action, effect, keep, drop[]}`` where action is:
          * ``discard`` — exactly one live copy and one or more from `.trash`/`.obsidian`; the
            stale copies go, the live note is untouched. (The common case: a note you deleted
            in Obsidian was indexed before it was removed.)
          * ``merge``   — several live copies; knowledge and relationships move onto the oldest
            and the others are removed.
          * ``gone``    — the entities no longer exist, so the notice is obsolete.
        """
        ids = [int(i) for i in payload.get("entity_ids", [])]
        sides = [s for s in (self._duplicate_side(i) for i in ids) if s]
        if len(sides) < 2:
            return {"action": "gone", "keep": None, "drop": [],
                    "effect": "These entities are already gone — accepting just clears the notice."}

        stale = [s for s in sides if s["stale"]]
        live = [s for s in sides if not s["stale"]]
        if len(live) == 1 and stale:
            n = sum(s["knowledge"] for s in stale)
            where = live[0]["source"] or live[0]["name"]
            return {
                "action": "discard", "keep": live[0], "drop": stale,
                "effect": (f"Deletes {len(stale)} stale cop{'y' if len(stale) == 1 else 'ies'} "
                           f"indexed from your trash, and {n} knowledge row"
                           f"{'' if n == 1 else 's'} that came with them. "
                           f"{where} is left untouched."),
            }
        keep, *drop = sorted(sides, key=lambda s: (s["created_at"] or "", s["id"]))
        moved = sum(s["knowledge"] for s in drop)
        return {
            "action": "merge", "keep": keep, "drop": drop,
            "effect": (f"Moves {moved} knowledge row{'' if moved == 1 else 's'} onto "
                       f"{keep['source'] or keep['name']} and removes the other "
                       f"{len(drop)} cop{'y' if len(drop) == 1 else 'ies'}. Nothing is lost."),
        }

    def _duplicate_side(self, entity_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, type, name, canonical_key, source, created_at FROM entities WHERE id=?",
                (entity_id,)).fetchone()
            if not row:
                return None
            n = conn.execute("SELECT COUNT(*) c FROM knowledge WHERE entity_id=?",
                             (entity_id,)).fetchone()["c"]
        side = dict(row)
        path = (side.get("source") or side.get("canonical_key") or "").lower()
        side["knowledge"] = n
        side["stale"] = any(d in path for d in self._STALE_DIRS)
        return side

    def apply_duplicate_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the plan ``duplicate_plan`` describes. Returns what actually happened."""
        plan = self.duplicate_plan(payload)
        if plan["action"] == "gone":
            return {"action": "gone", "removed": 0, "moved": 0}
        keep_id = plan["keep"]["id"]
        moved = removed = 0
        for side in plan["drop"]:
            if plan["action"] == "merge":
                moved += self.merge_entities(keep_id, side["id"])
            else:
                self.discard_entity(side["id"])
            removed += 1
        self.add_event("duplicate_resolved",
                       {"action": plan["action"], "keep": keep_id,
                        "removed": [s["id"] for s in plan["drop"]], "moved": moved})
        return {"action": plan["action"], "keep": keep_id, "removed": removed, "moved": moved}

    def merge_entities(self, keep_id: int, drop_id: int) -> int:
        """Move ``drop``'s knowledge and relationships onto ``keep``, then delete it.

        Knowledge that would collide with a row ``keep`` already has is dropped rather than
        duplicated (its vector goes too). Returns the number of knowledge rows carried over.
        """
        if keep_id == drop_id:
            return 0
        orphaned: list[str] = []
        with self._connect() as conn:
            rows = conn.execute("SELECT id, key, value FROM knowledge WHERE entity_id=?",
                                (drop_id,)).fetchall()
            moved = 0
            for r in rows:
                clash = conn.execute(
                    "SELECT id FROM knowledge WHERE entity_id=? AND key=? AND value=?",
                    (keep_id, r["key"], r["value"])).fetchone()
                if clash:
                    conn.execute("DELETE FROM knowledge WHERE id=?", (r["id"],))
                    orphaned.append(f"k:{r['id']}")
                else:
                    conn.execute("UPDATE knowledge SET entity_id=? WHERE id=?", (keep_id, r["id"]))
                    moved += 1
            conn.execute("UPDATE relationships SET src_id=? WHERE src_id=?", (keep_id, drop_id))
            conn.execute("UPDATE relationships SET dst_id=? WHERE dst_id=?", (keep_id, drop_id))
            conn.execute("UPDATE events SET entity_id=? WHERE entity_id=?", (keep_id, drop_id))
            conn.execute("DELETE FROM relationships WHERE src_id=dst_id")
            conn.execute("DELETE FROM entities WHERE id=?", (drop_id,))
        vectors.remove(orphaned + [f"e:{drop_id}"])
        return moved

    def discard_entity(self, entity_id: int) -> int:
        """Delete an entity and everything hanging off it. Returns rows of knowledge removed."""
        with self._connect() as conn:
            kids = [f"k:{r['id']}" for r in conn.execute(
                "SELECT id FROM knowledge WHERE entity_id=?", (entity_id,))]
            conn.execute("DELETE FROM knowledge WHERE entity_id=?", (entity_id,))
            conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
        vectors.remove(kids + [f"e:{entity_id}"])
        return len(kids)

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
