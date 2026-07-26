# CLAUDE.md — Orion

Guidance for Claude Code working in this repo. Read the three docs in `docs/` first; they are
the source of truth and this file is the operational summary.

## Versioning — non-negotiable

**Every user-requested change ends with a release.** Repo: `git@github.com:MoKaif/Orion.git`
(`origin/main`). After making and verifying the change, cut it with:

```bash
python scripts/release.py {major|minor|patch} -m "headline" [-a added] [-c changed] [-f fixed] [-r removed]
```

That one command bumps `__version__` in `orion/__init__.py` (the single source of truth —
`/health`, `/docs`, and the UI shell all read it), inserts the `CHANGELOG.md` entry, commits,
creates an annotated `vX.Y.Z` tag, and pushes `main --follow-tags`. **Never hand-edit
`__version__` or `CHANGELOG.md`.** Pick the bump per `docs/VERSIONING.md`: MINOR for new
capability (a new plugin/interface/job/provider/view — the common case), PATCH for
fixes/tuning/docs/config, MAJOR only for platform-contract breaks. When in doubt, bump larger.
Pass `--trailer "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"` on releases you cut.

## What Orion is

A single-user **Personal Knowledge Operating System**: a continuously running cognitive system
whose permanent asset is a **World Model** of the user's life/knowledge. The LLM is a routed,
cost-metered utility. Target host: an always-on **~8GB RAM, CPU-only Arch Linux** laptop —
local-first, cloud (Gemini Flash) only as a metered fallback. See `docs/ORION_MANIFESTO.md`,
`docs/ARCHITECTURE.md`, `docs/ROADMAP.md`.

## Status: M0–M5 complete. The platform is complete — new capability is now a new plugin, zero core changes.

- **M1** World Model: entities, knowledge (fact/observation/idea + confidence + status),
  relationships, events, workspaces, **review-inbox lifecycle**, hybrid semantic+keyword recall,
  Obsidian ingestion, streaming chat that consults the world model before answering.
- **M2** Orchestrator: cognitive-mode selection + best-fit **specialist** delegation
  (Software/Research/Generalist as plugins), **RAG-based tool selection**, tools with a
  **confirm gate** for irreversible actions.
- **M3** Background lifecycle: idle-by-default cron scheduler, proactive review generation
  (duplicates, briefings, re-mined knowledge), foreground/background gate (chat preempts jobs).

The live paths are now verified on the Arch host: `scripts/smoke_test.py` passes (fastembed +
sqlite-vec recall, a real Ollama turn, a full end-to-end pipeline run) and the M4 web UI serves.
Note: the local Ollama tag is **`qwen2.5:3b`** (config/models.json points here), not the
`-instruct-q4_K_M` suffix the older docs mention — the default `qwen2.5:3b` *is* that build.

**Provider split (cloud orchestrator):** all chat modes (`reflex`/`reasoning`/`deep_work`)
route to **DeepSeek `deepseek-v4-flash`** (`providers/deepseek.py`, OpenAI-compatible REST,
$0.14/M in · $0.28/M out · $0.0028/M cached input, automatic server-side context caching —
the usage block splits cache hits/misses and the router prices each side). Background
`dispatch`/`compress`/`embed` stay local on Ollama/fastembed. Escalation chain:
deepseek → gemini (unkeyed) → ollama. **Anthropic is wired but disabled** — the account had
a $0 credit balance as of 2026-07-22 (`providers.anthropic.enabled=false`; to revive: buy
credits, re-enable, and point routing back — `mode_models` still tiers Haiku/Sonnet/Opus;
note `effort` is stripped for Haiku, which 400s on it). `DEEPSEEK_API_KEY` loads from
gitignored `config/secrets.json` (also mirrored to `~/.config/deepseek/credentials.env`
for reuse across projects) — never from tracked config. Gemini/DeepSeek are planned cheaper-cloud fallbacks. The
`anthropic` SDK is lazy-imported and the router escalates local↔cloud, so a missing package,
missing key, or billing/availability failure **falls back to local** rather than crashing.
`ANTHROPIC_API_KEY` loads from gitignored `config/secrets.json` (also mirrored to
`~/.config/anthropic/credentials.env` for reuse across projects) — never from tracked config.

## On-host bring-up (Arch)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # fastapi, httpx, fastembed, sqlite-vec, croniter, ...
ollama pull qwen2.5:3b && ollama serve   # local reflex model (default = instruct Q4_K_M)
# optional cloud fallback:  echo '{"GEMINI_API_KEY":"..."}' > config/secrets.json   (gitignored)
python scripts/smoke_test.py             # verify live paths
python run.py                            # serve http://127.0.0.1:8000  (/ = mission control, /docs = Swagger)
```

Set the Obsidian vault path in `config/settings.json` (`vault.path`), then `POST /ingest/vault`.

## Architecture (modular monolith: small core + plugins)

```
orion/core/  config · constitution · events · cognition · orchestrator · gate · scheduler
             maintenance · dispatcher · plugins · identity
             world_model/{store,schema,vectors,extract} · providers/{base,router,ollama,anthropic,gemini}
             tools/{base,registry,selection,builtin} · api/app.py
plugins/     knowledge · research · software   (each has manifest.json + register())
interfaces/web/   minimal shell today — the real UI is M4
config/      settings · models · memory · tools · ui · jobs   (JSON; NO secrets)
data/        orion.db (SQLite + sqlite-vec) + usage.json   (gitignored)
```

**The pipeline (immutable, in `orchestrator.handle_turn`):** consult world model → pick
cognitive mode + specialist → RAG-select tools → maybe run one (confirm gate) → route to the
cost-appropriate provider → stream → persist → extract candidate knowledge to the review inbox.

## Conventions (do not break these)

- **No secrets in tracked files.** Cloud keys come from env / `config/secrets.json` (gitignored).
- **Everything degrades gracefully.** No Ollama / no fastembed / no croniter must never crash a
  request — fall back (keyword recall, skip tool dispatch, manual-run jobs) and log.
- **Never silently mutate the world model.** Inferences go through `review_inbox`
  (`ingest_candidate` applies the confidence policy). This is the anti-pollution guarantee.
- **Everything except core is a plugin.** A plugin ships `manifest.json` + a `register()` hook
  that wires tools/specialists/jobs into the core registries.
- **Irreversible actions require confirmation** (`identity.needs_approval`; constitution list).
- **Keep files readable.** No 1000-line modules; match the existing style.

## Key HTTP endpoints

`/health` · `POST /chat/stream` (SSE) · `/chat/history` · `/sessions` · `/reviews` +
`POST /reviews/{id}` · `POST /confirm/{id}` · `/tools` · `/specialists` · `/jobs` +
`POST /jobs/{name}/run` · `POST /ingest/vault`

## The UI: React SPA is what's served; Jinja lives at `/legacy`

`GET /` serves `interfaces/spa/dist` when it's built (the Dockerfile builds it in a Node stage),
falling back to the Jinja shell otherwise; the old HTMX mission control stays reachable at
`/legacy`. The SPA (`interfaces/spa/`, React + react-router + react-query + lucide, vendored
tokens in `src/styles/tokens.css`) consumes the `/api/*` JSON routes; the `/ui/*` fragment routes
still serve `/legacy`. **After changing SPA source, run `pnpm build` in `interfaces/spa`** or the
served bundle stays stale (`dist/` is gitignored and rebuilt in Docker).

## M4 web UI (Jinja, now `/legacy`) — where things live

`interfaces/web/` — design identity is **"Obsidian Archive"**: a digital card catalog
(obsidian green-black base, copper accent, serif display titles, index-card tabs on cards,
rubber-stamp fact/observation/idea marks, dotted ledger leaders; light "reading room" theme
via `[data-theme=light]`). `templates/index.html` is the shell (spine nav + ledger-line
telemetry + `#view`); `templates/fragments/*` are HTMX-swapped views (`dashboard`, `reviews`,
`sessions`, `chat`, `vitals`) sharing `_macros.html`. The **constellation/graph view was
removed** (`/ui/graph` + `/api/graph` gone; `world_model.graph_data()` remains but unused).
`static/style.css` is the design system, `static/app.js` owns SSE chat streaming + the ⌘K
palette, `static/vendor/htmx.min.js` is vendored (local-first, no runtime CDN). Server routes:
`GET /ui/*` return fragments, `POST /ui/reviews/{id}` resolves + re-renders the list.
Fragments render via a Jinja env built with `cache_size=0` (Py 3.14 + Jinja LRU-cache
workaround); Starlette's `TemplateResponse` takes `(request, name, context)`.

## M5 plugin SDK (done) — the completion of the platform

`orion/core/plugin_sdk.py` is the **one stable surface** a plugin's `register()` builds against;
plugins never import core registries directly. It covers every Manifesto §9 extension point:
`add_tool` · `add_specialist` · `add_agent` · `add_entity_type`/`add_relationship_type` (→
`world_model/types.py` registry) · `add_job` · `add_widget` (→ `core/widgets.py`, rendered in the
dashboard's plugin strip) · plus a module-level `router` a plugin exports, mounted at
`/plugins/<name>`. The loader (`plugins.py`) auto-registers manifest-declared types, mounts
routers, and warns on declared-vs-registered drift (including declared `agents`). Scaffolder:
`python -m orion create-plugin <name>` — generates a working tool **and** an agent with one
batched job. New endpoints: `/plugins` (contributions) · `/types` (world-model vocabulary). The
`knowledge` plugin is the reference implementation (rides entirely on the SDK).

## The inbox — one queue, and every card states its consequence

`orion/core/inbox.py` is a registry of **sources**; core contributes the world-model review
queue and plugins add their own via `plugin_sdk.add_inbox_source` (core no longer imports
Curator to build the inbox). Every card carries `title` · `effect` · `actions`, where **`effect`
says what accepting does** and each action's label names the outcome, not the mechanism
(`inbox_action(label, value, tone, confirm=…)`; `confirm` forces a second click on destructive
ones). This existed because a `duplicate` notice used to render as "record this observation
about undefined" and accepting it did **nothing at all** — `resolve_review` only ever committed
`knowledge`/`relationship`.

- **Duplicates are now real work.** `world_model.duplicate_plan(payload)` returns
  `{action, effect, keep, drop[]}` — `discard` when one side came from `.trash`/`.obsidian`,
  `merge` when both are live, `gone` when the entities are already deleted. Accept recomputes
  the plan server-side and runs it via `apply_duplicate_plan` → `merge_entities` /
  `discard_entity` (both clean up embeddings through the new `vectors.remove`).
- **Curator questions are answerable.** The `questions` table gained `kind`/`entity_id`/`alias`,
  so "no" actually splits an optimistically-merged alias back out (`entities._split_alias`) and
  "yes" confirms it; older rows are healed by a backfill that parses the question text.
- **Ingestion no longer manufactures duplicates**: `add_knowledge` upserts on
  (entity, key, value) — it used to append, so the hourly index gave every note a row per hour
  (94% of the table) — and `settings.vault.ignore` keeps `.trash`/`.obsidian` out. One-off
  cleanup: `python scripts/dedupe_knowledge.py [--apply]` (backs the db up first).
- **Route order matters**: the SPA catch-all is registered by `_register_spa_fallback()` at the
  *end* of startup. Registered at import time it shadowed every plugin GET route, which is why
  `/plugins/curator/questions` returned HTML while POSTs worked.

## Agents (revamped) — the unit the user sees

An **agent** is a named worker that owns background jobs and gets a card on `/agents` plus its own
page. `orion/core/agents.py` is the registry (`Agent`: title, tagline, blurb, icon, `accent` ∈
copper/fact/observation/idea, order, optional `summary()`/`detail()` callbacks, both called
defensively). Core ships **Conductor** (`maintenance.py` — consolidate + weekly briefing; named
Conductor because `orchestrator.py` already means the per-turn pipeline). The curator plugin ships
**Curator** (its five passes + `knowledge`'s `index_vault`, which declares `agent="curator"`).
There is no hardcoded job→agent map in core any more — a third agent is one `add_agent` call.

- `ScheduledJob` carries `agent` · `label` · `description` · `limit_default`; unknown agent names
  fall back to the Conductor. `job_limit(name, default)` is read **at run time**, so a batch-size
  change applies to the next run without a restart.
- API: `GET /api/agents` (cards) · `GET /api/agents/{agent}` (page: jobs + summary + the agent's
  own panels) · `POST /api/agents/{agent}/jobs/{job}/run` (queues, returns at once) ·
  `PATCH /api/agents/{agent}/jobs/{job}` (`enabled`/`cron`/`limit`; validates cron, writes the
  **gitignored `config/jobs.local.json` overlay**, reschedules live). `POST /jobs/{name}/run` still
  runs synchronously for scripts.
- Manual runs go through the gate as **non-preemptible** (they wait for an in-flight chat turn
  instead of being cancelled), with an honest `queued → running → idle` state on the job.
- `config.section()` now merges `<name>.local.json` over the tracked `<name>.json`; UI tuning goes
  to the overlay via `config.update_local()` so it never lands in a release diff.
- SPA views: `views/Agents.tsx` (cards only, no run buttons) → `views/AgentDetail.tsx` (run +
  per-pass schedule/batch/pause controls, the agent's panels, merged run log). `components/shift.tsx`
  draws the 24-hour **shift strip** from each job's cron (`src/cron.ts` parses/describes it).

## Curator plugin (v1 done)

`plugins/curator/` — the vault's resident editor, riding entirely on the SDK. A nightly
`curate_vault` job (03:00) scans the Obsidian vault (`settings.json > vault.path`, currently
`/home/nox/Nox`) and proposes grammar/spelling fixes via the router (`Mode.REFLEX` → Haiku,
local fallback). Nothing touches a note until the user applies a proposal — staleness check
(sha) + timestamped backup to `data/curator_backups/` first. Own store: `data/curator.db`.
API: `GET/POST /plugins/curator/proposals[/{id}]`, `POST /plugins/curator/scan`; dashboard
widget `vault_curation`.

**Both SQLite files run in WAL with a 30 s `busy_timeout`** (`store.conn()`,
`world_model.store._connect`, `vectors._connect`). The gate only holds background work back from
*chat*, not from other jobs, so passes did overlap and the stock 5 s/rollback-journal setup
turned that into `database is locked`. On top of that, `engine._pass_lock` runs **one Curator pass
at a time** (backfill takes it once for all four and reports per-pass failures instead of dying on
the first). Anything that copies a db must use `conn.backup()`, not a file copy — a plain copy
loses commits still in the `-wal` sidecar. Registry keys: a name that is only honorifics
("Bhaiya") normalizes to `""`, which used to be inserted and then collide forever on
`entities.canonical UNIQUE` — such mentions are now dropped, inserts are `ON CONFLICT`-safe, and
a migration heals existing blank keys. Roadmap: v2 backlinking, v3 gap-finding questions, v4 drafting
entity/place notes. (The old standalone Windows-era `Curator/` dir at repo root is legacy.)

## Next: expansion (each is one plugin / one interface, no core work)

Content plugins (`operations`/`finance`/`health`/`calendar`) and a Pi/voice client. See
`docs/ROADMAP.md`.

**Cloud-brain hardening (done):** the cost core is now real. Prompt caching — the orchestrator
splits the stable system prefix (constitution + specialist, sent as a `cache_control` block) from
the volatile per-turn world-model context, so the cached prefix isn't invalidated each turn
(`Message.cacheable`; caching fires once the prefix crosses the model's ~4096-token minimum).
Per-model **$** tracking — `config/models.json` has a cache-aware `pricing` table; the router
costs each turn against the model that actually ran and records tokens **and** dollars split by
model in `data/usage.json` (legacy bare-int days migrate on write); the telemetry rail shows
cost-today + a per-model tooltip. Mode→model tiering — `providers.anthropic.mode_models` routes
`reasoning`→Sonnet 5 and `deep_work`→Opus 4.8 (mode flows router→provider; falls back to the
default `model`). All of it degrades gracefully — no pricing row ⇒ $0, no key ⇒ local fallback.
