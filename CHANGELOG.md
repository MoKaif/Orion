# Changelog

All notable changes to Orion are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/) as interpreted in [`docs/VERSIONING.md`](docs/VERSIONING.md).

The single source of truth for the current version is `__version__` in `orion/__init__.py`;
it is served at `/health` and shown in the web UI. Releases are cut with
`python scripts/release.py` — never bump by hand.

<!-- releases -->

## [0.7.0] — 2026-07-25

Agents are first-class: cards, per-agent pages, tunable passes

### Added
- orion/core/agents.py — an Agent registry (title, tagline, blurb, icon, accent, summary/detail callbacks); plugins declare one with plugin_sdk.add_agent and it becomes a card, no core changes
- Core ships the Conductor agent (consolidate + weekly briefing); the curator plugin ships Curator, which also owns knowledge's index_vault via agent="curator"
- Per-pass controls: PATCH /api/agents/{agent}/jobs/{job} sets enabled/cron/limit, validates the cron, and reschedules live
- config/<name>.local.json overlay layer merged over tracked config; UI tuning writes there via config.update_local, so it never lands in a release diff
- job_limit(name, default) read at run time, so a batch-size change applies to the next run without a restart
- 24-hour shift strip on each card, drawn from the jobs' cron (src/cron.ts + components/shift.tsx)
- create-plugin now scaffolds an agent with a batched job; manifests declare agents and the loader warns on drift

### Changed
- Agents view shows one card per agent with no run buttons; running and configuring moved to the agent's own page
- /api/agents returns a list of agent cards and /api/agents/{agent} a page payload (was a curator/other job split)
- Manual runs go through the foreground gate as non-preemptible and report queued -> running -> idle instead of bypassing it

### Fixed
- index_vault ran a synchronous vault walk inside an async job, pinning the event loop and freezing every request for the length of the run; it now runs in a worker thread
- resolve() warned once per request for a job naming a missing agent; now once per name

### Removed
- The hardcoded _VAULT_JOBS job-to-agent map in core/api/app.py

## [0.6.0] — 2026-07-25

First tracked release. Everything below existed before this repository had git history;
the entries are reconstructed from the milestone record in `docs/ROADMAP.md` and are
grouped by milestone rather than by release date.

### Added
- **Versioning + release discipline.** `CHANGELOG.md`, `docs/VERSIONING.md`, and
  `scripts/release.py` (bump → changelog → commit → tag → push in one command).
- **M5 — plugin SDK.** `orion/core/plugin_sdk.py` as the one stable surface for
  `register()`: tools, specialists, world-model entity/relationship types, jobs, dashboard
  widgets, and a plugin-exported `router` mounted at `/plugins/<name>`. Scaffolder
  `python -m orion create-plugin <name>`; new `/plugins` and `/types` endpoints.
- **Curator plugin v2.** The vault's resident editor: nightly `curate_vault` job, review-gated
  grammar/entity-registry/backlink/journal→world-model passes, staleness check plus timestamped
  backups before any write, own store at `data/curator.db`, `vault_curation` dashboard widget.
- **M4 — mission control web UI.** "Obsidian Archive" design system in `interfaces/web/`:
  HTMX-swapped fragments (dashboard, reviews, sessions, chat, vitals), telemetry rail, SSE
  streaming chat, ⌘K palette, light "reading room" theme, vendored HTMX (no runtime CDN).
- **M3 — background lifecycle.** Idle-by-default cron scheduler, proactive review generation
  (duplicate detection, briefings, re-mined knowledge), foreground/background gate so chat
  preempts jobs.
- **M2 — orchestrator & cognition.** The immutable pipeline in `orchestrator.handle_turn`:
  cognitive-mode selection, best-fit specialist delegation (Software/Research/Generalist as
  plugins), RAG-based tool selection, tool execution behind the constitution's confirm gate.
- **M1 — world model.** SQLite + `sqlite-vec` schema (entities, knowledge, relationships,
  events, workspaces, review inbox, sessions, messages), the knowledge lifecycle
  (fact/observation/idea + confidence + status), hybrid semantic+keyword recall via fastembed,
  Obsidian vault ingestion, and chat that consults the world model before answering.
- **M0 — foundation.** Core package boundaries, layered JSON config, in-process event bus,
  constitution loader, provider abstraction + cost-aware router, FastAPI app.
- **Deployment.** CPU-only two-stage `Dockerfile` (Node builds the SPA, Python 3.13 runtime),
  `docker-compose.yml`, and `orion.service` — run as a Docker service on `:8020` under the
  NoxCtl dashboard.

### Changed
- **Cloud brain is DeepSeek.** All chat modes (`reflex`/`reasoning`/`deep_work`) route to
  `deepseek-v4-flash` via `providers/deepseek.py`; escalation chain deepseek → gemini → ollama.
  Background `dispatch`/`compress`/`embed` stay local on Ollama/fastembed.
- **Cost core is real.** Prompt caching splits the stable system prefix from volatile per-turn
  context, `config/models.json` carries a cache-aware pricing table, and the router records
  tokens *and* dollars per model in `data/usage.json`.
- **Local model tag** is `qwen2.5:3b` (that default *is* the instruct Q4_K_M build).
- `.gitignore` now excludes all of `data/`, `node_modules/`, SPA `dist/`, and editor state.

### Removed
- Constellation/graph view (`/ui/graph`, `/api/graph`); `world_model.graph_data()` remains
  but is unused.

### Disabled
- **Anthropic provider** (`providers.anthropic.enabled=false`) — the account hit a $0 credit
  balance on 2026-07-22. Wiring and Haiku/Sonnet/Opus mode tiering are intact; re-enable after
  buying credits.
