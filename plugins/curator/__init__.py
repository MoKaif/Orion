"""Curator plugin — the vault's resident editor and memory-builder.

v1 fixed grammar. v2 is the full editor, all on the SDK and pinned to local Ollama + fastembed
(no cloud budget), with every change gated behind human review:

  * grammar   — spelling/punctuation proposals (classifier-aware; skips templates/quotes/code)
  * entities  — a resident registry of the vault's people/places/projects (embedding dedup)
  * backlinks — ``[[wikilinks]]`` proposals that light up Obsidian's graph view
  * memory    — mine dated journals into World Model knowledge (through the review inbox)

Each pass is an idle-by-default job; ``curator_backfill`` runs a bounded slice of all of them so
the existing backlog is worked down over a few nights. Everything wires through
``orion.core.plugin_sdk``; the module-level ``router`` mounts at /plugins/curator.
"""
from html import escape

from fastapi import APIRouter
from pydantic import BaseModel

from orion.core import plugin_sdk as orion

router = APIRouter()


def register() -> None:
    from . import engine

    # the four passes, staggered in the small hours (idle-by-default, all retunable in jobs.json)
    orion.add_job("curate_vault", "0 3 * * *", lambda: engine.scan(limit=5))
    orion.add_job("build_registry", "15 3 * * *", lambda: engine.build_registry(limit=8))
    orion.add_job("grow_memory", "30 3 * * *", lambda: engine.grow_memory(limit=8))
    orion.add_job("weave_graph", "45 3 * * *", lambda: engine.weave_backlinks(limit=8))
    orion.add_job("curator_backfill", "0 4 * * *", lambda: engine.backfill(per_pass=8))

    # world-model vocabulary this plugin contributes (person/project are core already)
    orion.add_entity_type("place", "A location the user visits or references.", plugin="curator")
    orion.add_entity_type("org", "A company or organization in the user's life.", plugin="curator")

    orion.add_widget("vault_curation", "Curator", _render_widget, plugin="curator")


# -- plugin API (mounted at /plugins/curator) ------------------------------
class ProposalAction(BaseModel):
    action: str  # apply | reject


class Answer(BaseModel):
    answer: str


@router.get("/proposals")
async def list_proposals(status: str = "pending", kind: str | None = None):
    from . import engine
    return engine.proposals(status, kind)


@router.post("/proposals/{pid}")
async def resolve_proposal(pid: int, body: ProposalAction):
    from . import engine
    return engine.resolve(pid, body.action)


@router.get("/entities")
async def list_entities(status: str = "pending"):
    from . import store
    c = store.conn()
    rows = [dict(r) for r in c.execute(
        "SELECT id, name, type, mentions, status, note_path FROM entities "
        "WHERE status=? ORDER BY mentions DESC", (status,))]
    c.close()
    return rows


@router.get("/questions")
async def list_questions():
    from . import entities, store
    c = store.conn()
    out = entities.open_questions(c)
    c.close()
    return out


@router.post("/questions/{qid}")
async def answer(qid: int, body: Answer):
    from . import entities, store
    c = store.conn()
    entities.answer_question(c, qid, body.answer)
    c.close()
    return {"ok": True}


@router.post("/scan")
async def scan_now(limit: int = 5):
    from . import engine
    return await engine.scan(limit=limit)


@router.post("/passes/{name}")
async def run_pass(name: str, limit: int = 8):
    """Trigger one pass on demand: grammar | registry | memory | backlinks | backfill."""
    from . import engine
    fns = {"grammar": engine.scan, "registry": engine.build_registry,
           "memory": engine.grow_memory, "backlinks": engine.weave_backlinks}
    if name == "backfill":
        return await engine.backfill(per_pass=limit)
    if name not in fns:
        return {"ok": False, "reason": f"unknown pass '{name}'"}
    return await fns[name](limit=limit)


# -- dashboard widget ------------------------------------------------------
def _render_widget() -> str:
    from . import engine

    c = engine.counts()
    pending = c.get("pending", 0)
    applied = c.get("applied", 0)
    tracked = c.get("notes_tracked", 0)
    mined = c.get("mined", 0)
    ents = c.get("entities", 0)
    questions = c.get("questions_open", 0)
    if not tracked and not pending:
        return ('<p class="empty">No notes scanned yet. Run the Curator jobs to give your '
                "vault an editor and start growing Orion's memory.</p>")

    rows = "".join(
        f'<li class="ws"><span class="ws-name">'
        + (f'<a class="note-link" href="{escape(p["obsidian_uri"])}" '
           f'title="Open in Obsidian">{escape(p["path"])}</a>'
           if p.get("obsidian_uri") else escape(p["path"]))
        + f'</span><span class="ws-facts">{escape(p.get("kind", "grammar"))} #{p["id"]}</span></li>'
        for p in engine.proposals()[:4]
    )
    summary = (f'<li class="ws"><span class="ws-name">{pending} proposal'
               f'{"s" if pending != 1 else ""} awaiting review</span>'
               f'<span class="ws-facts">{applied} applied · {ents} entities · '
               f'{mined} notes mined</span></li>')
    q = (f'<li class="ws"><span class="ws-name">{questions} open question'
         f'{"s" if questions != 1 else ""} for you</span>'
         f'<span class="ws-facts">gap-finding</span></li>' if questions else "")
    review = ('<button class="btn link-more" hx-get="/ui/agents/curate_vault" '
              'hx-target="#view" hx-swap="innerHTML">review fixes →</button>' if pending else "")
    return f'<ul class="ws-list">{summary}{q}{rows}</ul>{review}'
