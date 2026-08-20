# Changelog

All notable changes to Orion are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/) as interpreted in [`docs/VERSIONING.md`](docs/VERSIONING.md).

The single source of truth for the current version is `__version__` in `orion/__init__.py`;
it is served at `/health` and shown in the web UI. Releases are cut with
`python scripts/release.py` — never bump by hand.

<!-- releases -->

## [Unreleased]

### Added
- Treasurer can ask FinStrive to scan recent HDFC transaction-alert emails and
  create unmapped reconciliation candidates every 30 minutes.
- Mailbox scan results and failures are retained in Treasurer's job history for
  review in the Orion UI.

### Changed
- Treasurer's FinStrive integration now exposes one narrowly scoped mutation:
  the reconciliation-candidate mailbox scan. It still cannot map, skip, edit or
  delete transactions.
- Mailbox scans use a dedicated 120-second timeout so IMAP retrieval can finish
  without weakening the shorter timeout used by ordinary transaction reads.

## [0.11.3] — 2026-08-16

Correct Maintainer verification history

### Fixed
- Migrate legacy missing-SDK and missing-dependency results to verification unavailable

## [0.11.2] — 2026-08-16

Make Maintainer runs reliable

### Fixed
- Stop runs when dependency installation fails and cache only complete installs
- Select verification from changed paths instead of unrelated project toolchains
- Report unavailable toolchains separately from code build failures
- Restore purple-buccaneers fetches through its HTTPS remote

## [0.11.1] — 2026-08-16

Fix Curator and automate Maintainer audits

### Changed
- Run nightly Maintainer audits on unchanged repositories with rotating source inspection
- Move Maintainer execution from Claude Code to Codex

### Fixed
- Protect Curator metadata and wikilinks during grammar review
- Invalidate stale backlink checkpoints when the entity registry changes

## [0.11.0] — 2026-08-03

Maintainer proposes code changes and opens pull requests you review

### Added
- Maintainer agent (plugins/maintainer): reads your projects nightly, proposes work you approve in the inbox, then hands it to Claude Code on the host and opens a pull request. It never merges and never writes to a checkout you use.
- scripts/maintainer_runner.py + orion-maintainer.service: a host-side runner that claims approved work over HTTP, runs Claude Code in a throwaway worktree, verifies with the repo's own build, and pushes a maintainer/* branch. Orion's container executes nothing.
- orion/core/reports.py + plugin_sdk.add_report_source: a plugin can contribute sections, facts and alerts to another agent's letters without importing it.

### Changed
- Herald's briefing, weekly letter and alert watch now carry Maintainer's news — pull requests waiting, runs overnight, Claude time spent.
- The workspace is bind-mounted read-only into the container and git is in the image, so the scan reads each repo at origin/<base> rather than off your working tree.

## [0.10.1] — 2026-08-01

Background jobs run on your clock, not the container's

### Changed
- The image installs tzdata so zone names resolve, and compose deliberately leaves TZ unset so settings.json stays the single source of truth for both bare-metal and container.
- docker-compose notes that data/ now carries Herald's outbox and config/ carries its Gmail app password, and that outbound SMTP needs no extra config under host networking.

### Fixed
- Every cron time and quiet-hours window is wall-clock, but the Docker image is UTC and the scheduler compares against a naive datetime.now() — so the 07:30 briefing fired at 13:00 IST and Herald's 22:00-07:00 quiet hours covered 03:30-12:30, suppressing the very slot the briefing was meant to use. config.apply_timezone() now runs first thing at startup and makes settings.json's identity.timezone authoritative; an explicit TZ still wins.

## [0.10.0] — 2026-08-01

Herald sounds like a correspondent, not an echo of yourself

### Added
- Optional GMAIL_SENDER_ADDRESS / GMAIL_SENDER_APP_PASSWORD let Herald send from its own Google account. Sending from your address to your address makes Gmail file every briefing as mail you sent yourself — your avatar, 'me' as the sender, no importance signal — and no header can fix that, because Gmail rewrites a From it did not authenticate.
- Priority headers on the letters that act on you (alerts and nudges by default, tunable via high_priority).
- The agent page says so plainly when Herald is sending from your own address.

### Changed
- Every subject is now built in one place: 'Orion · Herald — what happened · date'. Fixed prefix so it is recognisable and filterable in a single Gmail rule; the tail still answers whether it needs opening; the date keeps two identical mornings from threading together.
- is_self is keyed to the owner's mailbox rather than the sending account, so once Herald has its own address, mail addressed to Herald is still held for approval.

### Removed
- Auto-Submitted: auto-generated. Its only benefit was suppressing vacation auto-responders, which is meaningless for mail you send yourself, while marking the message machine-generated is the exact signal that argues against the importance we want.

## [0.9.1] — 2026-08-01

Herald's overnight section reads like a summary, not a log

### Changed
- The prose model is given the per-job tally rather than a bare run count, so it can describe what ran instead of counting executions.

### Fixed
- The morning briefing listed every job run individually, so thirteen hourly vault-index runs buried the four passes that actually did something. Runs now collapse to one row per job ('Vault search index ... ok ×13'), with anything that failed sorted to the top.

## [0.9.0] — 2026-08-01

Herald carries Orion out of the browser

### Added
- Herald, a third agent: an outbound Gmail mail service. A morning briefing (what ran overnight, what is waiting, what it cost), alerts when a job fails or spend crosses your line, a Monday letter, and one nudge when the review queue starts to rot.
- The send gate is drawn by recipient: mail to your own account goes unattended, anything addressed elsewhere is held in the outbox with its full text on an inbox card until you release it. send_email was already in the constitution's irreversible actions; mailer.deliver is what enforces it.
- A send_email tool, so a chat turn can ask Orion to mail something — confirm-gated at the orchestrator and again at the recipient.
- config/herald.json: daily cap, quiet hours, alert thresholds and nudge timing, all retunable per machine through the local overlay.
- A mail log and held-message panel on /agents/herald, and a herald_mail dashboard widget.

### Changed
- Letters compute their own figures and ask deepseek-v4-flash only for the prose around them, so the model cannot invent a count — and a letter still goes out figures-only when the provider is down or over budget.
- The inbox resolver routes on the item's origin rather than a closed list, so a plugin can contribute cards core has never heard of.

## [0.8.1] — 2026-07-26

Curator passes stop colliding with themselves and the db

### Fixed
- database is locked: both SQLite files now run in WAL with a 30s busy_timeout, and only one Curator pass runs at a time (the gate only fenced background work off from chat, not from other jobs)
- UNIQUE constraint failed: entities.canonical — a name made only of honorifics ('Bhaiya') normalized to an empty key, so the second one could never be inserted; those mentions are now dropped, inserts are ON CONFLICT-safe, and a migration heals existing blank keys
- backfill reports each pass's outcome instead of aborting the remaining three when one fails
- dedupe_knowledge backs up via sqlite's backup API — a file copy loses commits still in the -wal sidecar

## [0.8.0] — 2026-07-26

The inbox says what accepting will do

### Added
- orion/core/inbox.py — a source registry for the unified inbox; plugins contribute items via plugin_sdk.add_inbox_source instead of core importing Curator
- Every card now carries title / effect / actions: effect states the consequence in the user's words, and each button names its outcome ("Discard the stale copy") rather than a mechanism
- world_model.duplicate_plan() decides between discard (one copy came from .trash) / merge (both live) / gone (already deleted), shows both sides with their paths and fact counts, and accept recomputes and runs exactly that plan
- merge_entities / discard_entity, plus vectors.remove() so deleted rows stop occupying recall slots
- Curator questions are answerable from the inbox: 'no' splits an optimistically-merged alias back out into its own entity, 'yes' confirms it; questions carry kind/entity_id/alias, and older rows are healed by a backfill that parses the question text
- scripts/dedupe_knowledge.py — reports by default, --apply backs the db up then collapses duplicates and drops .trash entities

### Changed
- settings.vault.ignore keeps .trash/.obsidian out of vault ingestion, so deleted notes stop reappearing as duplicate entities

### Fixed
- Accepting a duplicate notice did nothing at all — resolve_review only committed knowledge/relationship items, so the card was unreadable and the action was a no-op
- add_knowledge appended instead of upserting on (entity, key, value): the hourly vault index gave every note a fresh copy of its content each run, leaving 94% of the knowledge table duplicated and crowding recall
- Answering a Curator question only stored the text; nothing acted on it
- The SPA catch-all route was registered at import time and shadowed every plugin GET route, so /plugins/curator/questions returned HTML instead of JSON; it is now registered after plugins mount

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
