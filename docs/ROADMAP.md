# Orion Roadmap

Milestones with explicit exit criteria. Each milestone is usable on its own; nothing is
over-built ahead of need. Checked against the [Manifesto](./ORION_MANIFESTO.md).

---

## M0 — Foundation & scaffolding  ← current

Establish the small core, clean boundaries, and a runnable app before any intelligence.

- [x] Repo skeleton: `orion/core/` package boundaries, `plugins/`, `interfaces/web/`.
- [x] Living docs: Manifesto, Architecture, Roadmap.
- [x] Layered JSON config loader.
- [x] In-process event bus (the event-driven spine).
- [x] Constitution loader.
- [x] Provider abstraction (`BaseProvider`) + cost-aware router skeleton (local-first + fallback).
- [x] FastAPI app with `/health` and a placeholder chat route.
- [x] `.gitignore` that keeps secrets out; env-based key loading.

**Exit:** `python run.py` boots, serves `/health`, loads config + constitution, and the module
boundaries match the architecture doc. (Ports of the real Ollama/Gemini adapters land here or
early M1.)

## M1 — World Model foundation  ✅ (dev-verified; live LLM/vector paths pending on the Arch host)

The heart of the system.

- [x] SQLite schema: `entities`, `knowledge`, `relationships`, `events`, `workspaces`,
      `review_inbox`, `sessions`, `messages`.
- [x] Entity / relationship CRUD through a clean `WorldModel` interface.
- [x] Knowledge lifecycle: kind (fact/observation/idea) + confidence + status; confidence
      policy (auto-accept / review / discard); Review Inbox with Accept / Edit / Reject.
- [x] Semantic recall: `sqlite-vec` + `fastembed` + embedding lanes (replaces `NullRAG`),
      with a tokenized keyword fallback when the vector deps aren't installed.
- [x] Obsidian vault ingestion → note entities (as the `knowledge` plugin).
- [x] Context-assembly wired into chat: consult the world model before answering, stream via
      the cost-aware router, persist the exchange, extract candidates into the Review Inbox.
- [x] Real Ollama + Gemini adapters + local-first router with visible fallback + token budget.

**Exit (met):** Orion consults its world model before responding (`context` event precedes the
first token), extracts candidate knowledge from conversations/notes, and routes it through the
Review Inbox for approval. **On-host TODO:** `pip install fastembed sqlite-vec`, start Ollama,
add a Gemini key — to exercise live streaming and semantic recall (both degrade gracefully now).

## M2 — Orchestrator & cognition  ✅ (dev-verified)

- [x] The full reasoning pipeline (understand → consult → gaps → mode → delegate → maybe-run-tool
      → respond → update), with the tool result merged into the prompt.
- [x] Cognitive-mode selection (Reflex / Reasoning / Deep Work) driving model routing.
- [x] Real plugin loading via `register()` hooks; specialists + tools registered by plugins.
- [x] Specialists as plugins: **Software** and **Research** (+ core Generalist), scored per message.
- [x] RAG-based tool selection (top-K + always-available set), with a keyword fallback.
- [x] Tool execution with the constitution's **confirm gate** for irreversible actions
      (`confirm` event → `POST /confirm/{id}`), plus `read_file`/`shell`/`web_search`/vault tools.

**Exit (met):** requests are classified into a mode + specialist, routed to the cost-appropriate
model, and delegated; only top-K relevant tool schemas are considered; irreversible tools require
approval. Verified via `/chat/stream` (`context` event carries mode + specialist) and `/tools`,
`/specialists`, `/confirm`.

## M3 — Background lifecycle  ✅ (dev-verified)

- [x] Cron scheduler, idle-by-default, config-driven (`config/jobs.json`): hourly `index_vault`,
      nightly `consolidate`, weekly `weekly_briefing`. Degrades to manual-run if croniter absent.
- [x] Proactive Review-Inbox generation: duplicate-entity detection, LLM-assisted knowledge
      re-mining of recent conversation, and a weekly briefing — all surfaced as reviewable items.
- [x] Foreground/background local-model gate: entering an interactive turn **preempts** running
      background jobs (verified: a running job is cancelled the moment chat starts).
- [x] `GET /jobs`, `POST /jobs/{name}/run` for inspection and on-demand triggering.

**Exit (met):** Orion does useful work while unattended (scheduled consolidation + briefings) and
surfaces reviewable discoveries; chat always wins the single local pipe.

## M4 — UX / mission control  ✅ (dev-verified over HTTP; visual pass pending in a browser)

HTMX-first mission control (vendored htmx, no build step) + minimal vanilla JS for the three
things HTMX can't do: SSE chat streaming, the command palette, and the constellation.

- [x] Dashboard: discoveries (review inbox front-and-centre), active workspaces, recently-learned
      knowledge (kind-coloured), and an activity feed — one HTMX fragment (`/ui/dashboard`).
- [x] Telemetry rail: live vitals (pending discoveries, knowledge/entities/links, tokens-today vs
      budget, local/cloud reachability) polled every 15s (`/ui/vitals`).
- [x] Real streaming chat pane: `fetch`+SSE over `/chat/stream`, surfacing the chosen cognitive
      mode + specialist, tool runs, provider fallbacks, the **confirm gate** (approve/cancel inline),
      and knowledge queued for review.
- [x] Sessions UI (`/ui/sessions` → open a thread into chat), command palette (⌘/Ctrl-K: navigate,
      ingest vault, run jobs, toggle theme), token/status visibility in the rail.
- [x] Knowledge-graph view — **the Constellation**: entities as stars sized by connectivity,
      relationships as lines, rendered client-side from `/api/graph`.
- [x] Semantic knowledge-kind colour system (fact=teal / observation=violet / idea=amber) used
      consistently across dashboard, reviews, and graph; light/dark themes.
- [x] Responsive layout (nav rail collapses to a bottom bar on mobile).
- [ ] **Tailscale exposure** — remaining host step (bind + `tailscale serve`); the UI is
      already responsive for phone use.

**Exit:** a mixed dashboard-first / chat experience. All routes verified live on the Arch host
(fragments render, SSE streams, review accept/reject round-trips, telemetry is live). Still to do:
eyeball the visual pass in a browser, then put it on Tailscale for phone access.

## M5 — Plugin SDK  ✅ (platform complete; expansion is now incremental)

- [x] Hardened plugin contract: the manifest auto-registers declared entity/relationship
      types, and a declared-vs-registered consistency check warns on drift.
- [x] Stable **Plugin SDK** (`orion.core.plugin_sdk`) — the one surface a plugin's `register()`
      builds against, covering all six §9 extension points: tools, specialists, entity/
      relationship types, background jobs, dashboard widgets, and API routes (a module-level
      `router` mounted at `/plugins/<name>`).
- [x] `python -m orion create-plugin <name>` scaffolder — generates a loadable plugin with a
      working example tool.
- [x] Introspection endpoints: `/plugins` (what each plugin contributes), `/types` (the
      plugin-extensible world-model vocabulary).

**Exit (met):** new capability = a new plugin, zero core changes. The `knowledge` plugin now
rides entirely on the SDK (tools + types + job + a `Vault knowledge` dashboard widget) as the
reference implementation.

## Beyond M5 — expansion (each item is one plugin / one interface, no core work)

- [ ] Content plugins: `operations`, `health`, `calendar` specialists + tools.
- [x] **Treasurer / finance** (`plugins/finance/`) — reads FinStrive without changing it,
      learns personal expected-spending ranges locally, detects category and transaction
      anomalies, constrains LLM interpretation to computed evidence, and contributes findings
      to chat, the Review Inbox, mission control, and Herald.
- [ ] Raspberry Pi / voice client as another thin interface to the same backend.
- [x] **Maintainer** (`plugins/maintainer/`) — the first agent that changes code outside Orion.
      DeepSeek reads each project overnight and proposes work; you approve the brief in the
      inbox; **Codex on the host** does the engineering in a throwaway worktree; a pull
      request comes back for you to review. It never merges and never writes to a checkout you
      use. The container gets the workspace read-only and does no execution at all — the hands
      are `scripts/maintainer_runner.py`, a `systemd --user` service that claims work over HTTP.
      One core addition (`orion/core/reports.py` + `plugin_sdk.add_report_source`) lets a plugin
      contribute sections, facts and alerts to another agent's letters, which is how Herald
      reports Maintainer's nights without knowing Maintainer exists.
      Next: chat-initiated tasks, Orion self-improvement (one config flag), PR-comment iteration.
- [x] **Cloud orchestrator hardening** — prompt caching (cacheable stable system prefix,
      separated from the volatile per-turn world-model context), per-model **$** cost tracking
      (cache-aware pricing table; `usage.json` records tokens + dollars split by model; the
      telemetry rail shows cost-today + a per-model tooltip), and mode→model tiering
      (`providers.anthropic.mode_models`: `reasoning`→Sonnet 5, `deep_work`→Opus 4.8).

---

## Non-negotiables checked every milestone

- Runs comfortably on 8GB CPU-only. · Local-first; cloud only on explicit/escalated calls.
- No secrets in the repo. · Human approval before irreversible actions.
- Core stays small; new capability = a plugin. · No file grows past readability.
