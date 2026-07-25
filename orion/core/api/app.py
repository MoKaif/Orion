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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from orion import __version__
from orion.core import agents, identity, inbox, orchestrator, plugins
from orion.core.config import config
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
    _register_spa_fallback()                  # last, so it can't shadow a plugin's routes
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
         "specialists": p.specialists, "agents": p.agents, "entity_types": p.entity_types,
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
    return [{"name": j.name, "agent": j.agent, "cron": j.cron, "last_run": j.last_run,
             "next_run": j.next_run, "running_since": j.running_since,
             "queued_since": j.queued_since, "enabled": scheduler.enabled(j.name),
             "preemptible": j.foreground_preemptible} for j in scheduler.jobs()]


@app.post("/jobs/{name}/run")
async def run_job(name: str):
    """Run a job and wait for the result (scripting path; the UI uses the queued route)."""
    return await scheduler.run_now(name)


# -- JSON API for the React SPA -------------------------------------------
# The SPA consumes these; the /ui/* Jinja fragments below stay until the SPA is at parity.
def _job_dict(job, runs: int = 0) -> dict:
    """One job as the UI sees it: what it does, when it runs, how the last run went."""
    hist = scheduler.history(job.name)
    last = hist[0] if hist else None
    return {
        "name": job.name, "label": job.display_label, "description": job.description,
        "agent": job.agent, "cron": job.cron, "enabled": scheduler.enabled(job.name),
        "limit": job.limit, "limit_default": job.limit_default,
        "next_run": job.next_run, "last_run": job.last_run,
        "running_since": job.running_since, "queued_since": job.queued_since,
        "last_ok": (last.get("ok") if last else None),
        "last_result": (last.get("result") if last else None),
        "last_seconds": (last.get("seconds") if last else None),
        "run_count": len(hist),
        "runs": hist[:runs] if runs else [],
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
    """One card per registered agent: who it is, how its passes are doing, what's waiting.

    Core knows nothing about any particular agent — Curator and Conductor arrive through the
    same registration a third agent will use.
    """
    return [_agent_card(a) for a in agents.all_agents()]


@app.get("/api/agents/{name}")
async def api_agent_detail(name: str):
    """One agent's page: its passes with their controls, its own panels, and the run log."""
    agent = agents.get(name)
    if agent is None:
        return JSONResponse({"error": f"unknown agent '{name}'"}, status_code=404)
    # the agent's own panels go alongside the core keys, never over them
    panels = {k: v for k, v in agents.detail_of(agent).items()
              if k not in ("agent", "summary", "jobs")}
    return {
        **panels,
        "agent": agent.card(),
        "summary": agents.summary_of(agent),
        "jobs": [_job_dict(j, runs=8) for j in _agent_jobs(name)],
    }


@app.post("/api/agents/{name}/jobs/{job_name}/run")
async def api_run_job(name: str, job_name: str):
    """Queue one of this agent's passes. Returns at once; the page polls for progress."""
    job = _job(job_name)
    if job is None or job.agent != name:
        return JSONResponse({"error": f"'{job_name}' is not one of {name}'s passes"},
                            status_code=404)
    return scheduler.request_run(job_name)


class JobPatch(BaseModel):
    enabled: bool | None = None
    cron: str | None = None
    limit: int | None = None


@app.patch("/api/agents/{name}/jobs/{job_name}")
async def api_patch_job(name: str, job_name: str, body: JobPatch):
    """Retune one pass: pause it, change its schedule, or change how much it does per run.

    Writes the gitignored config/jobs.local.json overlay (never the tracked defaults) and
    reschedules immediately, so a change takes effect without a restart.
    """
    job = _job(job_name)
    if job is None or job.agent != name:
        return JSONResponse({"error": f"'{job_name}' is not one of {name}'s passes"},
                            status_code=404)

    patch: dict = {}
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if body.cron is not None:
        cron = body.cron.strip()
        if not _valid_cron(cron):
            return JSONResponse(
                {"error": f"{cron!r} isn't a schedule Orion can read. Use five cron fields, "
                          "like '0 3 * * *' for 03:00 daily."}, status_code=400)
        patch["cron"] = cron
    if body.limit is not None:
        if job.limit_default is None:
            return JSONResponse({"error": f"'{job_name}' doesn't work in batches"},
                                status_code=400)
        patch["limit"] = max(1, min(500, body.limit))

    if patch:
        config.update_local("jobs", {"jobs": {job_name: patch}})
        scheduler.apply_config()
    return _job_dict(_job(job_name), runs=8)


def _valid_cron(expr: str) -> bool:
    """True if croniter can read this schedule; falls back to a field count when it's absent."""
    try:
        from croniter import croniter
        return croniter.is_valid(expr)
    except ImportError:
        return len(expr.split()) == 5


def _agent_jobs(name: str) -> list:
    """This agent's jobs, in registration order, including any that named it as a fallback."""
    return [j for j in scheduler.jobs() if agents.resolve(j.agent).name == name]


def _agent_card(agent) -> dict:
    """An agent's card: identity + rolled-up state of its passes + its own headline numbers."""
    jobs_ = _agent_jobs(agent.name)
    nexts = sorted(j.next_run for j in jobs_ if j.next_run)
    lasts = sorted((j.last_run for j in jobs_ if j.last_run), reverse=True)
    failed = [j.name for j in jobs_
              if (scheduler.history(j.name) or [{}])[0].get("ok") is False]
    return {
        **agent.card(),
        "summary": agents.summary_of(agent),
        "job_count": len(jobs_),
        "paused": sum(1 for j in jobs_ if not scheduler.enabled(j.name)),
        "busy": any(j.running_since or j.queued_since for j in jobs_),
        "failing": failed,
        "next_run": nexts[0] if nexts else None,
        "last_run": lasts[0] if lasts else None,
        # the shift strip on the card is drawn from these — schedule as information
        "jobs": [{"name": j.name, "label": j.display_label, "cron": j.cron,
                  "enabled": scheduler.enabled(j.name), "next_run": j.next_run}
                 for j in jobs_],
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


#: What accepting a world-model item does, said in the user's terms. A card without one of
#: these is a card the user cannot judge — which is how "duplicate" ended up unreadable.
def _world_model_card(r: dict) -> dict:
    payload = r.get("payload") or {}
    item_type = r["item_type"]
    agent, label, uri = _provenance(payload.get("source"))
    card: dict = {
        "origin": "world_model", "id": r["id"], "item_type": item_type,
        "payload": payload, "confidence": r.get("confidence"),
        "created_at": r.get("created_at") or "",
        "prov_agent": agent, "prov_label": label, "prov_uri": uri,
        "action_url": f"/ui/reviews/{r['id']}",
        "actions": [inbox.action("Accept", "accept", "accept"),
                    inbox.action("Reject", "reject", "reject")],
    }

    if item_type == "knowledge":
        entity = payload.get("entity", "you")
        kind = payload.get("kind", "observation")
        card["title"] = f"Remember this {kind} about {entity}"
        card["effect"] = (f"Adds it to what Orion knows about {entity}, where it can surface "
                          f"in future conversations. You can change or remove it later.")
    elif item_type == "relationship":
        card["title"] = "Link two things together"
        card["effect"] = "Records the connection in the world model."
    elif item_type == "duplicate":
        plan = world_model.duplicate_plan(payload)
        name, etype = payload.get("name", "?"), payload.get("entity_type", "entity")
        card["title"] = f"Two “{name}” {etype}s look like the same thing"
        card["plan"] = plan
        card["effect"] = plan["effect"]
        obsolete = plan["action"] == "gone"
        verb = {"discard": "Discard the stale copy", "merge": "Merge them",
                "gone": "Clear this notice"}[plan["action"]]
        card["actions"] = [
            inbox.action(verb, "accept", "accept",
                         confirm=None if obsolete
                         else "This deletes rows. Click again to confirm."),
            inbox.action("Dismiss" if obsolete else "Keep both", "reject", "reject"),
        ]
    elif item_type == "discovery":
        card["title"] = payload.get("kind", "discovery").replace("_", " ").capitalize()
        card["body"] = payload.get("summary", "")
        card["effect"] = "Marks it read. Nothing in the world model changes."
        card["actions"] = [inbox.action("Got it", "accept", "accept"),
                           inbox.action("Dismiss", "reject", "reject")]
    else:
        card["title"] = item_type.replace("_", " ").capitalize()
        card["effect"] = "Files this notice away."
    return card


def _inbox_items() -> list[dict]:
    """The one queue: world-model inferences plus whatever plugins have registered, newest first.

    Core no longer reaches into Curator to build this — plugins contribute through
    ``plugin_sdk.add_inbox_source``.
    """
    items = [_world_model_card(r) for r in world_model.pending_reviews()]
    items += inbox.items()
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
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


def _agent_or_owner(name: str):
    """An agent by name, or — for an old /ui/agents/<job> link — the agent that owns that job."""
    agent = agents.get(name)
    if agent is not None:
        return agent
    job = _job(name)
    return agents.resolve(job.agent) if job is not None else None


@app.get("/ui/agents", response_class=HTMLResponse)
async def ui_agents(request: Request):
    return _fragment("fragments/agents.html", request,
                     cards=[_agent_card(a) for a in agents.all_agents()])


@app.get("/ui/agents/{name}", response_class=HTMLResponse)
async def ui_agent_detail(name: str, request: Request):
    agent = _agent_or_owner(name)
    if agent is None:
        return HTMLResponse("<p class='empty'>No agent by that name.</p>", status_code=404)
    return _fragment("fragments/agent_detail.html", request,
                     agent=agent.card(), summary=agents.summary_of(agent),
                     jobs=[_job_dict(j, runs=6) for j in _agent_jobs(agent.name)],
                     panels=agents.detail_of(agent))


@app.post("/ui/agents/{name}/run", response_class=HTMLResponse)
async def ui_agent_run(name: str, request: Request):
    """Queue a pass by job name and re-render its agent's page, which polls while it runs."""
    scheduler.request_run(name)
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


async def spa_fallback(full_path: str, request: Request):
    """SPA client-side routes (/inbox, /agents/…, /chat, …) — anything not matched by a real
    route falls through here and gets the SPA shell, so a hard refresh on a deep link works."""
    spa = _spa_index()
    if spa is not None:
        return spa
    return HTMLResponse("Not found", status_code=404)


def _register_spa_fallback() -> None:
    """Add the catch-all **after** plugin routers are mounted.

    Starlette matches routes in registration order, so a catch-all added at import time
    shadows everything mounted later during startup: every plugin GET route
    (``/plugins/curator/questions``, …) silently returned the SPA's HTML instead of JSON.
    POSTs were unaffected, which is why applying a proposal worked but reading questions
    didn't. Registering here, at the end of startup, is what keeps the two apart.
    """
    if any(getattr(r, "path", None) == "/{full_path:path}" for r in app.routes):
        return
    app.add_api_route("/{full_path:path}", spa_fallback, methods=["GET"],
                      response_class=HTMLResponse, include_in_schema=False)
