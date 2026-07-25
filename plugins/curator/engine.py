"""Curator engine — orchestrates every pass and stays the plugin's stable public surface.

v1 was one function (grammar ``scan``). v2 keeps that surface working (``scan`` · ``proposals``
· ``counts`` · ``resolve`` · ``obsidian_uri`` are still imported by the core API and widget) but
now drives four cooperating passes over the vault, each with its **own checkpoint** so it only
looks at new-or-changed notes:

    grammar   → kind='grammar' proposals   (fix the prose)
    entities  → registry rows              (learn the cast)  ─┐ feeds
    backlinks → kind='backlink' proposals  (light up the graph) ◄┘
    memory    → World Model review-inbox   (grow Orion's mind)

Nothing mutates the vault or the World Model without the user: prose/backlink edits wait as
proposals (backup + staleness check on apply), hub notes wait as entity_note proposals, and
memories wait in the review inbox. ``backfill`` runs a bounded slice of every pass so the
existing backlog gets worked down over a few idle nights instead of one expensive session.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from orion.core.config import config

from . import backlinks, entities, grammar, memory, notes, store

log = logging.getLogger("orion.curator")

_BACKUPS = config.root() / "data" / "curator_backups"

# re-exports so callers keep importing these from engine
obsidian_uri = notes.obsidian_uri
sha = notes.sha


# -- shared new-or-changed iterator ----------------------------------------
def _todo(c, column: str, limit: int, editable_only: bool = True,
          block_kinds: list[str] | None = None) -> Iterator[tuple[Path, str, str, str]]:
    """Yield (path, rel, text, sha) for notes unseen-or-changed since `column`'s checkpoint.

    ``block_kinds`` lists proposal kinds whose pending existence should skip a note — used by
    the editing passes (grammar/backlink) to avoid stacking edits on the same file. The
    entity/memory passes pass ``[]`` so a pending grammar fix never blocks mining.
    """
    vault = notes.vault()
    if vault is None:
        return
    seen = store.checkpoints(c, column)
    pending = store.pending_paths(c, block_kinds)
    yielded = 0
    for path in notes.walk(vault):
        if yielded >= limit:
            break
        rel = str(path.relative_to(vault))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        h = notes.sha(text)
        if seen.get(rel) == h or rel in pending:
            continue
        if editable_only and not notes.editable(notes.classify(text)):
            store.mark_note(c, rel, **{column: h})   # checkpoint so we never re-check it
            continue
        yielded += 1
        yield path, rel, text, h


# -- pass 1: grammar -------------------------------------------------------
async def scan(limit: int = 5) -> dict[str, Any]:
    """Grammar/spelling pass (the original nightly job). Kept name + return shape for compat."""
    if not await _local_ready():
        return {"ok": False, "reason": "local model (Ollama) unreachable — Curator is local-only"}
    c = store.conn()
    checked = proposed = 0
    for _path, rel, text, h in _todo(c, "sha", limit, block_kinds=["grammar"]):
        checked += 1
        result = await grammar.propose_for(text, rel)
        if result:
            corrected, diff = result
            store.add_proposal(c, rel, h, corrected, diff, kind="grammar")
            proposed += 1
        store.mark_note(c, rel, sha=h)
    c.close()
    log.info("curator grammar: %d checked, %d proposed", checked, proposed)
    return {"ok": True, "checked": checked, "proposed": proposed}


# -- pass 2: entity registry ----------------------------------------------
async def build_registry(limit: int = 5) -> dict[str, Any]:
    """Mine entity mentions from notes into the resident registry, then propose hub notes."""
    if not await _local_ready():
        return {"ok": False, "reason": "local model unreachable"}
    c = store.conn()
    checked = mentions = 0
    embed_cache: dict[str, list[float]] = {}
    for _path, rel, text, h in _todo(c, "entity_sha", limit, block_kinds=[]):
        checked += 1
        for mention in await entities.mine_note(text):
            entities.register_mention(c, mention, embed_cache)
            mentions += 1
        store.mark_note(c, rel, entity_sha=h)
    hubs = _propose_hub_notes(c)
    total = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    c.close()
    log.info("curator registry: %d notes, %d mentions, %d entities, %d hub notes proposed",
             checked, mentions, total, hubs)
    result: dict[str, Any] = {"ok": True, "checked": checked, "mentions": mentions,
                              "entities_total": total, "hub_notes": hubs}
    if hubs == 0:
        result["note"] = (
            f"{total} entities tracked. A hub note is proposed once one is seen "
            f"{entities._MENTION_THRESHOLD}+ times — none have yet, so run more notes "
            "or the Backfill pass to keep accumulating mentions."
            if total else "No entities found in these notes.")
    return result


def _propose_hub_notes(c) -> int:
    proposed = 0
    # skip paths we've already offered (pending) or the user already turned down (rejected)
    existing = {r["path"] for r in c.execute(
        "SELECT path FROM proposals WHERE kind='entity_note' "
        "AND status IN ('pending','rejected')")}
    for row in entities.hub_candidates(c):
        path = entities.note_path_for(row)
        if path in existing:
            continue
        md = entities.note_markdown(row)
        diff = grammar.diff("", md, path)
        store.add_proposal(c, path, "", md, diff, kind="entity_note")
        proposed += 1
    return proposed


# -- pass 3: backlinks (graph view) ---------------------------------------
async def weave_backlinks(limit: int = 5) -> dict[str, Any]:
    c = store.conn()
    registry = backlinks.linkable_registry(c)
    if not registry:
        c.close()
        return {"ok": True, "checked": 0, "proposed": 0,
                "note": "no approved entities yet — approve hub notes first"}
    checked = proposed = 0
    for _path, rel, text, h in _todo(c, "linked_sha", limit, block_kinds=["grammar", "backlink"]):
        checked += 1
        result = backlinks.propose_for(text, rel, registry)
        if result:
            woven, diff = result
            store.add_proposal(c, rel, h, woven, diff, kind="backlink")
            proposed += 1
        else:
            store.mark_note(c, rel, linked_sha=h)  # nothing to link → checkpoint now
    c.close()
    log.info("curator backlinks: %d checked, %d proposed", checked, proposed)
    return {"ok": True, "checked": checked, "proposed": proposed}


# -- pass 4: memory --------------------------------------------------------
async def grow_memory(limit: int = 5) -> dict[str, Any]:
    if not await _local_ready():
        return {"ok": False, "reason": "local model unreachable"}
    c = store.conn()
    checked = queued = accepted = 0
    for _path, rel, text, h in _todo(c, "mined_sha", limit, block_kinds=[]):
        checked += 1
        tally = await memory.mine_note(text, rel)
        queued += tally.get("queued", 0)
        accepted += tally.get("accepted", 0)
        store.mark_note(c, rel, mined_sha=h)
    c.close()
    log.info("curator memory: %d notes, %d queued, %d accepted", checked, queued, accepted)
    return {"ok": True, "checked": checked, "queued": queued, "accepted": accepted}


# -- backfill: bounded catch-up across every pass --------------------------
async def backfill(per_pass: int = 8) -> dict[str, Any]:
    """One bounded slice of each pass — run nightly to work the backlog down over time."""
    return {
        "grammar": await scan(per_pass),
        "registry": await build_registry(per_pass),
        "memory": await grow_memory(per_pass),
        "backlinks": await weave_backlinks(per_pass),
    }


async def _local_ready() -> bool:
    from . import llm
    return await llm.available()


# -- review surface (grammar + backlink + entity_note) ---------------------
def proposals(status: str = "pending", kind: str | None = None) -> list[dict[str, Any]]:
    c = store.conn()
    rows = store.proposals(c, status, kind)
    c.close()
    for r in rows:
        r["obsidian_uri"] = notes.obsidian_uri(r["path"])
    return rows


def counts() -> dict[str, int]:
    c = store.conn()
    out = store.counts(c)
    c.close()
    return out


def resolve(pid: int, action: str) -> dict[str, Any]:
    """Apply or reject a proposal of any kind (grammar · backlink · entity_note)."""
    c = store.conn()
    row = c.execute("SELECT * FROM proposals WHERE id=? AND status='pending'", (pid,)).fetchone()
    if row is None:
        c.close()
        return {"ok": False, "reason": "unknown or already-resolved proposal"}
    row = dict(row)

    if action != "apply":
        c.execute("UPDATE proposals SET status='rejected', resolved_at=? WHERE id=?",
                  (store.now(), pid))
        c.commit(); c.close()
        return {"ok": True, "outcome": "rejected", "path": row["path"]}

    vault = notes.vault()
    if vault is None:
        c.close()
        return {"ok": False, "reason": "no vault configured"}
    kind = row.get("kind", "grammar")
    try:
        if kind == "entity_note":
            result = _apply_entity_note(c, vault, row)
        else:
            result = _apply_edit(c, vault, row, kind)
    except Exception as e:
        c.close()
        return {"ok": False, "reason": str(e)}
    c.close()
    return result


def _apply_edit(c, vault: Path, row: dict[str, Any], kind: str) -> dict[str, Any]:
    """Apply a text edit (grammar/backlink) to an existing note, with backup + staleness check."""
    target = vault / row["path"]
    try:
        current = target.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "reason": f"cannot read note: {e}"}
    if notes.sha(current) != row["original_sha"]:
        c.execute("UPDATE proposals SET status='stale', resolved_at=? WHERE id=?",
                  (store.now(), row["id"]))
        c.commit()
        return {"ok": False, "reason": "note changed on disk since the proposal — rescan"}
    backup = _backup(current, row["path"])
    target.write_text(row["corrected_text"], encoding="utf-8")
    new_h = notes.sha(row["corrected_text"])
    col = "linked_sha" if kind == "backlink" else "sha"
    store.mark_note(c, row["path"], **{col: new_h})
    c.execute("UPDATE proposals SET status='applied', resolved_at=? WHERE id=?",
              (store.now(), row["id"]))
    c.commit()
    return {"ok": True, "outcome": "applied", "path": row["path"], "backup": str(backup)}


def _apply_entity_note(c, vault: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Create a new hub note, then mirror its entity into the World Model."""
    target = vault / row["path"]
    if target.exists():
        c.execute("UPDATE proposals SET status='stale', resolved_at=? WHERE id=?",
                  (store.now(), row["id"]))
        c.commit()
        return {"ok": False, "reason": "a note already exists at that path"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(row["corrected_text"], encoding="utf-8")
    # link the registry entity to its note + push it into the World Model
    eid = _entity_for_note(c, row["corrected_text"])
    if eid is not None:
        entities.mark_authored(c, eid, row["path"])
        entities.approve_into_world_model(c, eid)
    c.execute("UPDATE proposals SET status='applied', resolved_at=? WHERE id=?",
              (store.now(), row["id"]))
    c.commit()
    return {"ok": True, "outcome": "applied", "path": row["path"], "entity_id": eid}


def _entity_for_note(c, markdown: str) -> int | None:
    import re
    m = re.search(r"curator_entity_id:\s*(\d+)", markdown)
    return int(m.group(1)) if m else None


def _backup(content: str, rel: str) -> Path:
    backup = _BACKUPS / datetime.now().strftime("%Y-%m-%d_%H%M%S") / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(content, encoding="utf-8")
    return backup
