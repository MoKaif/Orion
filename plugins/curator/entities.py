"""The resident entity registry — the vault's cast of people, places, projects and orgs.

This is the keystone both other v2 features stand on: backlinking needs a whitelist of real
entities to link, and memory extraction needs resolved subjects to attach knowledge to. The
pass mines mentions from a note with the local model, then **resolves** each against the
existing registry so the same person is one row, not eighty:

1. normalize (strip honorifics like "sir"/"bhai", lowercase) → canonical key; exact or
   alias match merges deterministically;
2. otherwise an embedding-similarity check (≥ 0.90) catches surface variants keyword matching
   misses ("mummy" ≈ "mom") before a new row is created.

Frequently-seen entities become **hub-note proposals** (``People/Naufil.md`` with aliases in
frontmatter — which is what lights up Obsidian's graph) and, on approval, mirror into the
World Model as entities. Low-confidence resolutions become **gap questions** for the user.
"""
from __future__ import annotations

import json
import re
from typing import Any

from orion.core.world_model import world_model

from . import llm, store

_HONORIFICS = re.compile(
    r"\b(sir|madam|ma'?am|bhai|bhaiya|bhaiyya|ji)\b", re.IGNORECASE)
_TYPE_DIR = {"person": "People", "place": "Places", "project": "Projects", "org": "Orgs"}
_MENTION_THRESHOLD = 3     # appearances before a hub note is worth proposing
_MERGE_SIM = 0.90          # embedding cosine above which two names are the same entity

_SYSTEM = (
    "Extract named entities from the personal journal note below. Return JSON: "
    "{\"entities\": [{\"name\": str, \"type\": \"person|place|project|org\"}]}. "
    "The name must be ONLY the proper noun itself — no verbs, no articles, no surrounding "
    "words. Include people (friends, family, colleagues), places (cities, venues), "
    "projects/apps, organizations. Exclude games, books, foods, generic words, and the author. "
    "Example — input: \"Met Naufil at JP North. Worked on Finstrive.\" → "
    "{\"entities\": [{\"name\": \"Naufil\", \"type\": \"person\"}, "
    "{\"name\": \"JP North\", \"type\": \"place\"}, "
    "{\"name\": \"Finstrive\", \"type\": \"project\"}]}. "
    "Empty list if none. JSON only."
)

# leading words a small model sometimes glues onto a name; safe to strip (proper nouns
# almost never start with these).
_LEAD_NOISE = re.compile(
    r"^(met|saw|with|and|the|a|an|to|at|for|played|called|texted|visited|from)\s+",
    re.IGNORECASE)


def canonical(name: str) -> str:
    key = _HONORIFICS.sub("", name).lower()
    return re.sub(r"\s+", " ", key).strip(" -")


def display(name: str) -> str:
    """A clean display form — strip trailing honorifics but keep the given name."""
    return re.sub(r"\s+", " ", _HONORIFICS.sub("", name)).strip(" -") or name.strip()


async def mine_note(text: str) -> list[dict[str, str]]:
    data = await llm.extract(_SYSTEM, text)
    items = llm.as_list(data, "entities")
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("name") and it.get("type") in _TYPE_DIR:
            name = _LEAD_NOISE.sub("", str(it["name"]).strip()).strip()
            # a name that is nothing but honorifics ("Bhaiya", "Sir") normalizes to an empty
            # key — it names no one, so it never becomes a registry row.
            if len(name) >= 2 and canonical(name):
                out.append({"name": name, "type": it["type"]})
    return out


def _registry(c) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute("SELECT * FROM entities")]


def _alias_set(row: dict[str, Any]) -> set[str]:
    try:
        return {a.lower() for a in json.loads(row["aliases"])}
    except Exception:
        return set()


def resolve(c, mention: dict[str, str],
            embed_cache: dict[str, list[float]]) -> tuple[int | None, float]:
    """Find the registry id this mention belongs to. (id, score); (None, 0) if new."""
    canon = canonical(mention["name"])
    if not canon:
        return None, 0.0
    rows = _registry(c)
    for r in rows:                                   # exact canonical or known alias
        if r["canonical"] == canon or canon in _alias_set(r) \
                or mention["name"].lower() in _alias_set(r):
            return r["id"], 1.0
    vec = llm.embed(canon)                            # semantic variant?
    if vec is not None and rows:
        cand = {}
        for r in rows:
            cv = embed_cache.get(r["canonical"]) or llm.embed(r["canonical"])
            if cv is not None:
                embed_cache[r["canonical"]] = cv
                cand[str(r["id"])] = cv
        key, score = llm.most_similar(vec, cand)
        if key is not None and score >= _MERGE_SIM:
            return int(key), score
    return None, 0.0


def _create(c, canon: str, name: str, type: str, aliases: list[str]) -> int:
    """Insert a new registry row, or fold into whichever row already owns this key.

    ``ON CONFLICT`` matters because two passes can reach the same unseen name at the same
    time (backfill runs the registry pass too). A lost race should be a merge, not a
    ``UNIQUE constraint failed`` that takes the whole run down.
    """
    ts = store.now()
    c.execute(
        "INSERT INTO entities (canonical, name, type, aliases, mentions, status, "
        "created_at, updated_at) VALUES (?,?,?,?,1,'pending',?,?) "
        "ON CONFLICT(canonical) DO UPDATE SET mentions=mentions+1, updated_at=excluded.updated_at",
        (canon, name, type, json.dumps(aliases), ts, ts))
    c.commit()
    return c.execute("SELECT id FROM entities WHERE canonical=?", (canon,)).fetchone()["id"]


def register_mention(c, mention: dict[str, str],
                     embed_cache: dict[str, list[float]]) -> int | None:
    """Record one mention, merging into an existing entity or creating a pending one.

    Returns the registry id, or None for a mention that names no one.
    """
    canon = canonical(mention["name"])
    if not canon:               # honorifics only ("Bhaiya") — not an entity
        return None
    eid, score = resolve(c, mention, embed_cache)
    if eid is not None:
        row = c.execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone()
        old_aliases = set(json.loads(row["aliases"]))
        aliases = old_aliases | {mention["name"]}
        c.execute("UPDATE entities SET mentions=mentions+1, aliases=?, updated_at=? WHERE id=?",
                  (json.dumps(sorted(aliases)), store.now(), eid))
        c.commit()
        if row["status"] == "approved" and aliases != old_aliases:
            store.invalidate_backlinks(c)
        # A fuzzy (non-exact) resolve is worth confirming. The merge has *already* happened
        # optimistically, so the question records which entity absorbed which alias — that's
        # what lets "no, different" actually split them back apart.
        if 0 < score < 1.0:
            ask(c, display(mention["name"]),
                f'Is "{mention["name"]}" the same as "{row["name"]}"?',
                kind="same_as", entity_id=eid, alias=mention["name"])
        return eid
    return _create(c, canon, display(mention["name"]), mention["type"], [mention["name"]])


# -- hub-note authoring ----------------------------------------------------
def note_markdown(row: dict[str, Any]) -> str:
    aliases = [a for a in json.loads(row["aliases"]) if a != row["name"]]
    fm = ["---", f"type: {row['type']}", f"curator_entity_id: {row['id']}"]
    if aliases:
        fm.append("aliases: [" + ", ".join(f'"{a}"' for a in aliases) + "]")
    fm.append("---")
    body = [f"\n# {row['name']}\n",
            f"*{row['type'].capitalize()} — {row['mentions']} mentions across the journal.*\n",
            "## Notes\n", "## Backlinks\n"]
    return "\n".join(fm) + "\n" + "\n".join(body)


def hub_candidates(c) -> list[dict[str, Any]]:
    """Pending entities frequent enough to deserve a hub note (none authored yet)."""
    return [dict(r) for r in c.execute(
        "SELECT * FROM entities WHERE status='pending' AND note_path IS NULL "
        "AND mentions >= ? ORDER BY mentions DESC", (_MENTION_THRESHOLD,))]


def note_path_for(row: dict[str, Any]) -> str:
    safe = re.sub(r"[\\/:*?\"<>|]", "", row["name"]).strip()
    return f"{_TYPE_DIR[row['type']]}/{safe}.md"


def mark_authored(c, entity_id: int, note_path: str) -> None:
    c.execute("UPDATE entities SET note_path=?, updated_at=? WHERE id=?",
              (note_path, store.now(), entity_id))
    c.commit()


def approve_into_world_model(c, entity_id: int) -> int | None:
    """Mirror an approved registry entity into the World Model. Returns the wm id."""
    row = c.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        return None
    wm_id = world_model.upsert_entity(row["type"], row["name"], source="curator")
    c.execute("UPDATE entities SET status='approved', wm_entity_id=?, updated_at=? WHERE id=?",
              (wm_id, store.now(), entity_id))
    c.commit()
    store.invalidate_backlinks(c)
    return wm_id


def approved_entities(c) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM entities WHERE status='approved' ORDER BY mentions DESC")]


# -- gap questions ---------------------------------------------------------
def ask(c, subject: str, question: str, kind: str = "open",
        entity_id: int | None = None, alias: str | None = None) -> None:
    exists = c.execute(
        "SELECT 1 FROM questions WHERE subject=? AND question=? AND status='open'",
        (subject, question)).fetchone()
    if exists:
        return
    c.execute("INSERT INTO questions (subject, question, kind, entity_id, alias, status, "
              "created_at) VALUES (?,?,?,?,?, 'open', ?)",
              (subject, question, kind, entity_id, alias, store.now()))
    c.commit()


def open_questions(c) -> list[dict[str, Any]]:
    """Open questions, each carrying the entity it concerns so the UI can say what's at stake."""
    out = []
    for r in c.execute("SELECT * FROM questions WHERE status='open' ORDER BY id DESC"):
        q = dict(r)
        if q.get("entity_id"):
            row = c.execute("SELECT name, type, mentions, aliases FROM entities WHERE id=?",
                            (q["entity_id"],)).fetchone()
            if row:
                q["entity"] = {"name": row["name"], "type": row["type"],
                               "mentions": row["mentions"],
                               "aliases": json.loads(row["aliases"] or "[]")}
        out.append(q)
    return out


def answer_question(c, qid: int, answer: str) -> dict[str, Any]:
    """Record an answer **and act on it**.

    For a ``same_as`` question the merge already happened when the mention was seen, so:
      * "yes"  confirms it — the alias stays folded into the entity;
      * "no"   splits it — the alias is removed from that entity, its mention count is given
        back, and the surface form becomes its own pending entity.
    Anything else is kept as a free-text note on the question.
    """
    row = c.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if row is None:
        return {"ok": False, "reason": "no such question"}
    q = dict(row)
    verdict = (answer or "").strip().lower()
    outcome = "recorded"

    if q.get("kind") == "same_as" and q.get("entity_id") and q.get("alias"):
        if verdict in ("yes", "y", "same", "true"):
            outcome = "confirmed"
        elif verdict in ("no", "n", "different", "false"):
            outcome = _split_alias(c, int(q["entity_id"]), q["alias"], q.get("subject") or "")
    c.execute("UPDATE questions SET answer=?, status='answered', answered_at=? WHERE id=?",
              (answer, store.now(), qid))
    c.commit()
    return {"ok": True, "outcome": outcome}


def _split_alias(c, entity_id: int, alias: str, subject: str) -> str:
    """Undo an optimistic merge: pull ``alias`` off the entity and give it its own row."""
    row = c.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    if row is None:
        return "entity already gone"
    aliases = [a for a in json.loads(row["aliases"] or "[]") if a.lower() != alias.lower()]
    c.execute("UPDATE entities SET aliases=?, mentions=MAX(mentions-1, 0), updated_at=? WHERE id=?",
              (json.dumps(sorted(aliases)), store.now(), entity_id))
    if row["status"] == "approved":
        store.invalidate_backlinks(c)
    canon = canonical(alias)
    if not canon:
        return "split"          # the alias names no one on its own; nothing to split out to
    _create(c, canon, display(subject or alias), row["type"], [alias])
    return "split"
