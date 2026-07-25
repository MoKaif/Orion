"""FastAPI application — the HTTP surface. Routes stay thin and delegate into core.

M1: streaming chat that runs the full pipeline (consult world model -> stream -> persist ->
extract to review inbox), sessions, the review-inbox lifecycle, and a vault-ingest trigger.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from orion import __version__
from orion.core import identity, orchestrator, plugins
from orion.core.constitution import constitution
from orion.core.scheduler import scheduler
from orion.core.world_model import world_model

log = logging.getLogger("orion")
_WEB = Path(__file__).resolve().parents[3] / "interfaces" / "web"
_SPA_DIST = Path(__file__).resolve().parents[3] / "interfaces" / "spa" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Orion %s starting for user=%s", __version__, identity.user())
    from orion.core.tools.builtin import register_builtins
    from orion.core.tools import selection
    from orion.core import maintenance
    register_builtins()                       # always-available core tools
    loaded = plugins.load_all(app)            # tools + specialists + jobs + types + routes + widgets
    maintenance.register_core_jobs()          # nightly consolidate + weekly briefing
    indexed = selection.index_tools()         # embed tool descriptions for RAG selection
    log.info("plugins loaded: %s | tools indexed: %d", loaded or "none", indexed)
    await scheduler.start()                    # idle-by-default background lifecycle
    yield
    await scheduler.stop()


app = FastAPI(title="Orion", version=__version__, lifespan=lifespan)

if (_WEB / "static").exists():
    app.mount("/static", StaticFiles(directory=_WEB / "static"), name="static")

# The React SPA's hashed build assets (Vite emits everything under dist/assets).
if (_SPA_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_SPA_DIST / "assets"), name="spa-assets")


def _templates() -> Jinja2Templates | None:
    tpl_dir = _WEB / "templates"
    if not tpl_dir.exists():
        return None
    import jinja2
    # cache_size=0 disables Jinja's template LRU cache, whose weakref-keyed lookups trip
    # a hashing error under Python 3.14. Fragments are tiny, so re-parsing is negligible.
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(tpl_dir)),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
        cache_size=0,
    )
    return Jinja2Templates(env=env)


templates = _templates()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "user": identity.user(),
        "constitution_loaded": bool(constitution.text()),
        "plugins": [p.name for p in plugins.discover()],
    }


# -- chat -----------------------------------------------------------------
class ChatIn(BaseModel):
    message: str
    session_id: int | None = None


@app.post("/chat/stream")
async def chat_stream(body: ChatIn):
    session_id = body.session_id or world_model.create_session()

    async def sse():
        events: list[dict] = []
        yield _frame({"type": "start", "session_id": session_id})
        async for token in orchestrator.handle_turn(body.message, session_id, on_event=events.append):
            while events:
                yield _frame(events.pop(0))
            yield _frame({"type": "token", "text": token})
        while events:
            yield _frame(events.pop(0))
        yield _frame({"type": "done", "session_id": session_id})

    return StreamingResponse(sse(), media_type="text/event-stream")


def _frame(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


class ConfirmIn(BaseModel):
    approve: bool


@app.post("/confirm/{pid}")
async def confirm(pid: int, body: ConfirmIn):
    return await orchestrator.execute_confirmed(pid, body.approve)


@app.get("/tools")
async def tools():
    from orion.core.tools import registry
    return [{"name": t.name, "description": t.description,
             "requires_confirm": t.requires_confirm} for t in registry.all_tools()]


@app.get("/specialists")
async def specialists_list():
    from orion.core import specialists
    return [{"name": s.name, "description": s.description} for s in specialists.all_specialists()]


@app.get("/plugins")
async def plugins_list():
    """Discovered plugins + what each manifest contributes (mission-control introspection)."""
    return [
        {"name": p.name, "version": p.version, "tools": p.tools,
         "specialists": p.specialists, "entity_types": p.entity_types,
         "relationship_types": p.relationship_types,
         "background_jobs": [j.get("name") for j in p.background_jobs],
         "dashboard_widgets": p.dashboard_widgets, "permissions": p.permissions}
        for p in plugins.discover()
    ]


@app.get("/types")
async def types_list():
    """Registered entity + relationship types (plugin-extensible world-model vocabulary)."""
    from orion.core.world_model import types
    return types.all_types()


@app.get("/sessions")
async def sessions():
    return world_model.sessions()


@app.get("/chat/history")
async def chat_history(session_id: int):
    return world_model.history(session_id)


# -- review inbox (the knowledge lifecycle gate) --------------------------
class ReviewAction(BaseModel):
    action: str  # accept | edit | reject
    payload: dict | None = None


@app.get("/reviews")
async def reviews():
    return world_model.pending_reviews()


@app.post("/reviews/{review_id}")
async def resolve_review(review_id: int, body: ReviewAction):
    return world_model.resolve_review(review_id, body.action, body.payload)


# -- knowledge ingestion --------------------------------------------------
@app.post("/ingest/vault")
async def ingest_vault():
    from plugins.knowledge.ingest import ingest_vault as run
    return run()


@app.get("/jobs")
async def jobs():
    return [{"name": j.name, "cron": j.cron, "last_run": j.last_run,
             "next_run": j.next_run, "running_since": j.running_since,
             "preemptible": j.foreground_preemptible} for j in scheduler.jobs()]


@app.post("/jobs/{name}/run")
async def run_job(name: str):
    return await scheduler.run_now(name)


# -- JSON API for the React SPA -------------------------------------------
# The SPA consumes these; the /ui/* Jinja fragments below stay until the SPA is at parity.
def _job_dict(job, label: str | None = None) -> dict:
    hist = scheduler.history(job.name)
    last = hist[0] if hist else None
    return {
        "name": job.name, "label": label or job.name.replace("_", " "),
        "cron": job.cron, "next_run": job.next_run, "last_run": job.last_run,
        "running_since": job.running_since,
        "last_ok": (last.get("ok") if last else None),
        "last_result": (last.get("result") if last else None),
    }


@app.get("/api/vitals")
async def api_vitals():
    """Telemetry rail data (stats, provider health, usage $) as JSON."""
    return await _vitals()


@app.get("/api/inbox")
async def api_inbox():
    """The unified review queue — world-model inferences + Curator note edits — normalized."""
    return _inbox_items()


@app.get("/api/agents")
async def api_agents():
    """Background jobs, with every vault/Obsidian pass grouped under the Curator umbrella."""
    jobs_ = scheduler.jobs()
    by_name = {j.name: j for j in jobs_}
    return {
        "curator": {
            "pending": len(_curator_proposals() or []),
            "jobs": [_job_dict(by_name[n], _VAULT_JOBS[n]) for n in _VAULT_JOBS if n in by_name],
        },
        "other": [_job_dict(j) for j in jobs_ if j.name not in _VAULT_JOBS],
    }


@app.get("/api/agents/{name}")
async def api_agent_detail(name: str):
    """One agent: status, run log, and (for the Curator) its pending proposals + questions."""
    job = _job(name)
    if job is None:
        return {"error": "unknown agent"}
    is_curator_pass = name in _VAULT_JOBS
    return {
        "job": _job_dict(job, _VAULT_JOBS.get(name)),
        "runs": scheduler.history(name),
        "proposals": (_curator_proposals() or []) if is_curator_pass else [],
        "questions": _curator_questions() if is_curator_pass else [],
        "entities": _curator_entities() if name == "build_registry" else [],
    }


@app.get("/api/widgets")
async def api_widgets():
    """Plugin-contributed dashboard widgets, pre-rendered to HTML fragments."""
    from orion.core import widgets
    return widgets.render_all()


# -- M4 mission control: HTMX fragment views + telemetry data --------------
def _fragment(name: str, request: Request, **ctx):
    if templates is None:
        return HTMLResponse("<p>UI templates unavailable.</p>")
    return templates.TemplateResponse(request, name, ctx)


async def _vitals() -> dict:
    """Live system vitals for the telemetry rail."""
    from orion.core.providers import router
    from orion.core import specialists
    from orion.core.tools import registry
    ollama = router.get("ollama")
    deepseek = router.get("deepseek")
    anthropic = router.get("anthropic")
    gemini = router.get("gemini")
    return {
        "stats": world_model.stats(),
        "usage": router.usage(),
        "ollama_up": bool(ollama and await ollama.is_available()),
        "deepseek_up": bool(deepseek and await deepseek.is_available()),
        "anthropic_up": bool(anthropic and await anthropic.is_available()),
        "gemini_up": bool(gemini and await gemini.is_available()),
        "jobs": [{"name": j.name, "last_run": j.last_run} for j in scheduler.jobs()],
        "tools": len(registry.all_tools()),
        "specialists": len(specialists.all_specialists()),
    }


@app.get("/ui/vitals", response_class=HTMLResponse)
async def ui_vitals(request: Request):
    return _fragment("fragments/vitals.html", request, v=await _vitals())


@app.get("/ui/dashboard", response_class=HTMLResponse)
async def ui_dashboard(request: Request):
    from orion.core import widgets
    return _fragment(
        "fragments/dashboard.html", request,
        v=await _vitals(),
        reviews=_inbox_items(),
        workspaces=world_model.workspaces(),
        activity=world_model.recent_events(12),
        knowledge=world_model.recent_knowledge(8),
        widgets=widgets.render_all(),
    )


@app.get("/ui/reviews", response_class=HTMLResponse)
async def ui_reviews(request: Request):
    return _fragment("fragments/reviews.html", request, reviews=_inbox_items())


@app.post("/ui/reviews/{review_id}", response_class=HTMLResponse)
async def ui_resolve_review(review_id: int, request: Request):
    form = await request.form()
    world_model.resolve_review(review_id, form.get("action", "reject"))
    return _fragment("fragments/reviews.html", request, reviews=_inbox_items())


@app.post("/ui/inbox/curator/{pid}", response_class=HTMLResponse)
async def ui_inbox_curator(pid: int, request: Request):
    """Resolve a Curator note-edit proposal from *inside the inbox* (Apply/Reject), then
    re-render the unified inbox list — the twin of /ui/curator/{pid}, which re-renders the
    Curator agent page instead."""
    form = await request.form()
    try:
        from plugins.curator import engine as curator
        curator.resolve(pid, form.get("action", "reject"))
    except Exception as e:
        log.warning("curator inbox resolve failed: %s", e)
    return _fragment("fragments/reviews.html", request, reviews=_inbox_items())


@app.get("/ui/sessions", response_class=HTMLResponse)
async def ui_sessions(request: Request):
    return _fragment("fragments/sessions.html", request,
                     sessions=world_model.sessions())


@app.get("/ui/chat", response_class=HTMLResponse)
async def ui_chat(request: Request, session_id: int | None = None):
    history = world_model.history(session_id) if session_id else []
    return _fragment("fragments/chat.html", request,
                     session_id=session_id or "", history=history)


# -- inbox: unified review queue (world-model inferences + Curator note edits) --
# The vault/Obsidian jobs, all owned by Curator. Order = display order under the umbrella;
# value = the friendly sub-agent label shown in the Agents view.
_VAULT_JOBS = {
    "curate_vault":    "Grammar & spelling",
    "build_registry":  "Entity registry",
    "grow_memory":     "Journal → memory",
    "weave_graph":     "Backlinks",
    "index_vault":     "Vault search index",
    "curator_backfill": "Backfill (all passes)",
}


def _obsidian_uri(rel: str) -> str:
    """Deep link into the Obsidian app for a vault-relative note path (empty if unavailable)."""
    try:
        from plugins.curator import notes
        return notes.obsidian_uri(rel)
    except Exception:
        return ""


def _provenance(source: str | None) -> tuple[str, str, str]:
    """(agent, human label, obsidian-uri) for an inbox item's `source` string.

    This is the answer to 'where did this come from?': `vault:...` items are the Curator
    reading a note, `session:N` is a chat, `maintenance:*` is the nightly re-mining pass.
    """
    source = source or ""
    if source.startswith("vault:"):
        rel = source[len("vault:"):]
        name = rel.rsplit("/", 1)[-1].removesuffix(".md")
        kind = "journal note" if "Journal" in rel else "note"
        return ("Curator", f"your {kind} {name}", _obsidian_uri(rel))
    if source.startswith("session:"):
        return ("Chat", f"chat session #{source.split(':', 1)[1]}", "")
    if source.startswith("maintenance:"):
        return ("Consolidation", "the nightly re-mining of recent chat", "")
    return ("Orion", source or "an internal inference", "")


def _inbox_items() -> list[dict]:
    """The unified inbox: every world-model inference *and* every pending Curator note edit,
    each normalized to one shape (see the `review_card` macro) and sorted newest-first."""
    items: list[dict] = []
    for r in world_model.pending_reviews():
        payload = r.get("payload") or {}
        agent, label, uri = _provenance(payload.get("source"))
        items.append({
            "origin": "world_model", "id": r["id"], "item_type": r["item_type"],
            "payload": payload, "confidence": r.get("confidence"),
            "created_at": r.get("created_at") or "",
            "prov_agent": agent, "prov_label": label, "prov_uri": uri,
            "action_url": f"/ui/reviews/{r['id']}",
        })
    for p in (_curator_proposals() or []):
        path = p.get("path", "")
        name = path.rsplit("/", 1)[-1].removesuffix(".md")
        kind = "journal note" if "Journal" in path else "note"
        items.append({
            "origin": "curator", "id": p["id"], "kind": p.get("kind", "grammar"),
            "diff": p.get("diff", ""), "created_at": p.get("created_at") or "",
            "prov_agent": "Curator", "prov_label": f"your {kind} {name}",
            "prov_uri": p.get("obsidian_uri"),
            "action_url": f"/ui/inbox/curator/{p['id']}",
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


# -- agents: background jobs as first-class UI citizens --------------------
def _job(name: str):
    return next((j for j in scheduler.jobs() if j.name == name), None)


def _curator_proposals() -> list | None:
    """Pending Curator proposals, or None if the plugin isn't loaded."""
    try:
        from plugins.curator import engine as curator
        return curator.proposals()
    except Exception:
        return None


def _curator_questions() -> list:
    """Open Curator gap-questions, or [] if the plugin isn't loaded."""
    try:
        from plugins.curator import entities, store
        c = store.conn()
        out = entities.open_questions(c)
        c.close()
        return out
    except Exception:
        return []


def _curator_entities(limit: int = 40) -> list:
    """The Curator's resident entity registry (most-mentioned first), or [] if unavailable."""
    try:
        from plugins.curator import store
        c = store.conn()
        rows = [dict(r) for r in c.execute(
            "SELECT id, name, type, mentions, status, note_path FROM entities "
            "ORDER BY mentions DESC, id DESC LIMIT ?", (limit,))]
        c.close()
        return rows
    except Exception:
        return []


@app.get("/ui/agents", response_class=HTMLResponse)
async def ui_agents(request: Request):
    jobs_ = scheduler.jobs()
    by_name = {j.name: j for j in jobs_}
    # Curator owns every Obsidian/vault pass; the rest stand on their own.
    curator_jobs = [{"job": by_name[n], "label": _VAULT_JOBS[n]}
                    for n in _VAULT_JOBS if n in by_name]
    other_jobs = [j for j in jobs_ if j.name not in _VAULT_JOBS]
    proposals = _curator_proposals() or []
    return _fragment("fragments/agents.html", request,
                     curator_jobs=curator_jobs, other_jobs=other_jobs,
                     runs={j.name: scheduler.history(j.name) for j in jobs_},
                     pending={"curator": len(proposals)})


@app.get("/ui/agents/{name}", response_class=HTMLResponse)
async def ui_agent_detail(name: str, request: Request):
    job = _job(name)
    if job is None:
        return HTMLResponse("<p class='empty'>Unknown agent.</p>", status_code=404)
    proposals = _curator_proposals() if name == "curate_vault" else None
    questions = _curator_questions() if name == "curate_vault" else []
    return _fragment("fragments/agent_detail.html", request, job=job,
                     runs=scheduler.history(name), proposals=proposals, questions=questions)


@app.post("/ui/agents/{name}/run", response_class=HTMLResponse)
async def ui_agent_run(name: str, request: Request):
    """Fire a job without blocking the UI; the detail view polls while it runs."""
    import asyncio
    job = _job(name)
    if job is not None and not job.running_since:
        asyncio.create_task(scheduler.run_now(name))
        await asyncio.sleep(0.2)   # let running_since set so the re-render shows it
    return await ui_agent_detail(name, request)


@app.post("/ui/curator/{pid}", response_class=HTMLResponse)
async def ui_curator_resolve(pid: int, request: Request):
    form = await request.form()
    try:
        from plugins.curator import engine as curator
        curator.resolve(pid, form.get("action", "reject"))
    except Exception as e:
        log.warning("curator resolve failed: %s", e)
    return await ui_agent_detail("curate_vault", request)


@app.post("/ui/curator/questions/{qid}", response_class=HTMLResponse)
async def ui_curator_answer(qid: int, request: Request):
    form = await request.form()
    answer = (form.get("answer") or "").strip()
    try:
        from plugins.curator import entities, store
        c = store.conn()
        if answer == "__dismiss__" or not answer:
            entities.answer_question(c, qid, "")  # dismiss = answered-empty
            c.execute("UPDATE questions SET status='dismissed' WHERE id=?", (qid,))
            c.commit()
        else:
            entities.answer_question(c, qid, answer)
        c.close()
    except Exception as e:
        log.warning("curator answer failed: %s", e)
    return await ui_agent_detail("curate_vault", request)


def _spa_index() -> HTMLResponse | None:
    """The built React SPA's entry document, or None if it hasn't been built yet."""
    idx = _SPA_DIST / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text(encoding="utf-8"))
    return None


@app.get("/legacy", response_class=HTMLResponse)
async def legacy_index(request: Request):
    """The previous HTMX/Jinja mission control, kept reachable during the SPA transition."""
    if templates is None:
        return HTMLResponse(f"<h1>Orion {__version__}</h1><p>see /health, /docs</p>")
    return templates.TemplateResponse(request, "index.html", {"version": __version__})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _spa_index() or await legacy_index(request)


# SPA client-side routes (/inbox, /agents/…, /chat, …) — anything not matched by an API,
# /ui, /static, /assets, or /docs route falls through here and gets the SPA shell so a hard
# refresh on a deep link still works. Registered last so it never shadows a real endpoint.
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa_fallback(full_path: str, request: Request):
    spa = _spa_index()
    if spa is not None:
        return spa
    return HTMLResponse("Not found", status_code=404)
