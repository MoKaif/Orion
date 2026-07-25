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

    orion.add_agent(
        "curator", "Curator",
        tagline="Obsidian vault",
        blurb="Reads your vault the way an editor would: fixes the prose, learns the cast, links "
              "the notes together, and mines your journal for things worth remembering. Nothing "
              "reaches a note or your world model until you approve it.",
        icon="book-open", accent="copper", plugin="curator", order=20,
        summary=_summary, detail=_detail)

    # the passes, staggered in the small hours (idle-by-default; cron + batch size retunable
    # per job from the Curator's page, which writes config/jobs.local.json)
    def pass_job(name: str, cron: str, fn, default_limit: int, label: str, description: str):
        orion.add_job(name, cron,
                      lambda: fn(orion.job_limit(name, default_limit)),
                      agent="curator", label=label, description=description,
                      limit_default=default_limit)

    pass_job("curate_vault", "0 3 * * *", engine.scan, 5, "Grammar & spelling",
             "Proposes spelling and punctuation fixes for notes that changed, skipping "
             "templates, quotes and code.")
    pass_job("build_registry", "15 3 * * *", engine.build_registry, 8, "Entity registry",
             "Learns the people, places and projects your notes talk about, and offers a hub "
             "note once one appears three times.")
    pass_job("grow_memory", "30 3 * * *", engine.grow_memory, 8, "Journal → memory",
             "Mines dated journal entries for durable facts and queues them for your review.")
    pass_job("weave_graph", "45 3 * * *", engine.weave_backlinks, 8, "Backlinks",
             "Proposes [[wikilinks]] between notes so the graph view fills in. Uses approved "
             "entities only.")
    orion.add_job("curator_backfill", "0 4 * * *",
                  lambda: engine.backfill(orion.job_limit("curator_backfill", 8)),
                  agent="curator", label="Backfill",
                  description="Runs one slice of every pass above, to work the backlog down "
                              "over a few nights.",
                  limit_default=8)

    # world-model vocabulary this plugin contributes (person/project are core already)
    orion.add_entity_type("place", "A location the user visits or references.", plugin="curator")
    orion.add_entity_type("org", "A company or organization in the user's life.", plugin="curator")

    orion.add_widget("vault_curation", "Curator", _render_widget, plugin="curator")

    # everything of the Curator's that waits on the user, in the one inbox
    orion.add_inbox_source("curator", _inbox_items, plugin="curator")


# -- what mission control shows for this agent -----------------------------
def _summary() -> dict:
    """Headline numbers for the Curator's card and page."""
    from . import engine

    c = engine.counts()
    return {
        "pending": c.get("pending", 0),
        "metrics": [
            {"label": "notes read", "value": c.get("notes_tracked", 0)},
            {"label": "entities", "value": c.get("entities", 0)},
            {"label": "journals mined", "value": c.get("mined", 0)},
            {"label": "applied", "value": c.get("applied", 0)},
        ],
    }


def _detail() -> dict:
    """Extra panels for the Curator's page: what's waiting, what it wants to ask, who it knows."""
    from . import engine, entities as ents, store

    c = store.conn()
    try:
        questions = ents.open_questions(c)
        registry = [dict(r) for r in c.execute(
            "SELECT id, name, type, mentions, status, note_path FROM entities "
            "ORDER BY mentions DESC, id DESC LIMIT 40")]
    finally:
        c.close()
    return {"proposals": engine.proposals(), "questions": questions,
            "entities": registry, "hub_threshold": ents._MENTION_THRESHOLD}


# -- the Curator's share of the inbox --------------------------------------
_EDIT_BLURB = {
    "grammar": "a spelling and punctuation fix",
    "backlink": "new [[wikilinks]] between your notes",
    "entity_note": "a new hub note",
}


def _inbox_items() -> list[dict]:
    """Note edits and open questions, each saying plainly what accepting will do."""
    from . import engine, entities as ents, store

    items: list[dict] = []
    for p in engine.proposals():
        path = p.get("path", "")
        name = path.rsplit("/", 1)[-1].removesuffix(".md")
        kind = p.get("kind", "grammar")
        what = _EDIT_BLURB.get(kind, "a note edit")
        effect = (f"Creates {path} in your vault." if kind == "entity_note"
                  else f"Rewrites {path} with the changes below. The current version is backed "
                       f"up to data/curator_backups/ first, and nothing else in the note moves.")
        items.append({
            "origin": "curator", "id": p["id"],
            "title": f"Curator proposes {what}",
            "kind": kind, "diff": p.get("diff", ""),
            "effect": effect,
            "created_at": p.get("created_at") or "",
            "prov_agent": "Curator",
            "prov_label": f"your note {name}",
            "prov_uri": p.get("obsidian_uri"),
            "action_url": f"/plugins/curator/proposals/{p['id']}",
            "actions": [
                orion.inbox_action("Create note" if kind == "entity_note" else "Apply the edit",
                                   "apply", "accept"),
                orion.inbox_action("Leave it alone", "reject", "reject"),
            ],
        })

    c = store.conn()
    try:
        questions = ents.open_questions(c)
    finally:
        c.close()
    for q in questions:
        entity = q.get("entity") or {}
        alias, ename = q.get("alias"), entity.get("name")
        if q.get("kind") == "same_as" and alias and ename:
            body = (f"While reading your notes the Curator treated “{alias}” as another way of "
                    f"writing “{ename}”, and folded it in. It wasn't certain, so it's asking.")
            effect = (f"Saying they're the same keeps “{alias}” as an alias of “{ename}” "
                      f"({entity.get('mentions', 0)} mentions). Saying they're different pulls "
                      f"“{alias}” back out into its own {entity.get('type', 'entity')}.")
            actions = [
                orion.inbox_action(f"Same as {ename}", "yes", "accept"),
                orion.inbox_action("Different things", "no", "reject"),
                orion.inbox_action("Skip", "__dismiss__"),
            ]
        else:
            body = q.get("question", "")
            effect = "Your answer is kept with the question; nothing in the vault changes."
            actions = [orion.inbox_action("Skip", "__dismiss__")]
        items.append({
            "origin": "curator_question", "id": q["id"],
            "title": q.get("question", "A question about your notes"),
            "body": body, "effect": effect, "answerable": True,
            "created_at": q.get("created_at") or "",
            "prov_agent": "Curator",
            "prov_label": f"the name “{q.get('subject', '')}”",
            "action_url": f"/plugins/curator/questions/{q['id']}",
            "actions": actions,
        })
    return items


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
    """Answer a gap question. 'yes' confirms the merge, 'no' splits it back apart,
    '__dismiss__' sets it aside without deciding."""
    from . import entities, store
    c = store.conn()
    try:
        if body.answer.strip() in ("", "__dismiss__"):
            c.execute("UPDATE questions SET status='dismissed', answered_at=? WHERE id=?",
                      (store.now(), qid))
            c.commit()
            return {"ok": True, "outcome": "dismissed"}
        return entities.answer_question(c, qid, body.answer)
    finally:
        c.close()


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
    review = ('<a class="btn link-more" href="/agents/curator">review fixes →</a>'
              if pending else "")
    return f'<ul class="ws-list">{summary}{q}{rows}</ul>{review}'
