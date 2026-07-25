# Orion Architecture

Technical translation of the [Manifesto](./ORION_MANIFESTO.md) into a buildable system.
Target host: **an always-on ~8GB RAM, CPU-only Arch Linux laptop.** That constraint drives
every decision here.

---

## 0. The governing tradeoff

> The **World Model — not the LLM — is the product.** The LLM is a routed, mostly-cloud,
> cost-metered utility. Expensive, high-frequency work stays local and small.

An 8GB CPU-only box cannot run a good reasoning model. So work is split by *what it demands*,
not by *where it's convenient*.

## 1. Model & cost strategy

| Work | Runs on | Cost |
|---|---|---|
| Embeddings (semantic memory, RAG, tool selection) | **Local** — `fastembed` (`bge-small-en-v1.5`, ONNX, ~50MB, 384-dim) | $0 |
| Reflex: intent classify, tool-arg extraction, entity/relationship extraction, dedupe, tag, summarize | **Local** — Qwen2.5 3B Instruct `Q4_K_M` via Ollama (~2GB) | $0 |
| Reasoning: design, code review, plan, research synthesis | **Cloud** — Gemini 2.5 Flash | ~free → cheap |
| Deep Work: create project, multi-step analysis, refactor | **Cloud** — Gemini Flash, escalate to Claude Haiku for tool-use/coding | metered |

**Provider strategy:** OpenAI-compatible-first (borrowed from odysseus). One generic adapter
serves any OpenAI-style endpoint (OpenRouter, Groq, vLLM, Claude via its own adapter). Default
cloud fallback = **Gemini 2.5 Flash**. Provider detected by **hostname, not substring**.

**Cost controls, built into the router from day one:**

- **Local-first escalation ladder** — try local; escalate to cloud only when the task is flagged
  complex or local confidence is low.
- **RAG-based tool selection** — retrieve only top-K relevant tool schemas per message (+ a tiny
  always-available set) instead of injecting all of them. The single biggest prompt-size saver.
- **Rolling summaries + aggressive context trimming.**
- **Daily token/cost budget** in the router with a hard ceiling (extends the MVP's rate limiter).
- **Semantic response cache** to skip near-duplicate calls.
- **Visible, pre-content-only fallback** — switch providers only before the first token; emit a
  `fallback` event so a broken primary is never silently masked.

## 2. Architecture style: modular monolith, small core + plugins

Single process, clean module boundaries enforced from day one. **Rule: modules communicate
through interfaces, not implementations.** No cross-layer circular-import hacks; no multi-
thousand-line files.

```
orion/
├── core/
│   ├── config.py            layered JSON config (settings/models/memory/tools/ui)
│   ├── constitution.py      loads & versions the governing doc
│   ├── events.py            in-process pub/sub event bus (the event-driven spine)
│   ├── world_model/         ★ the heart: entities, relationships, knowledge lifecycle, vectors
│   ├── cognition.py         Reflex / Reasoning / Deep-Work mode selection
│   ├── orchestrator.py      the context-assembly pipeline (§5)
│   ├── providers/           BaseProvider + Ollama + Gemini + generic OpenAI-compat + router
│   ├── tools/               registry + dispatcher + RAG tool selection
│   ├── scheduler.py         background lifecycle, idle-by-default
│   ├── plugins.py           manifest discovery + registration
│   ├── identity.py          single-user + human-approval gates
│   └── api/                 FastAPI routes (thin; delegate into core)
├── plugins/                 knowledge, software, research, operations (+ calendar/finance/health later)
├── interfaces/web/          dashboard (mission control) + chat — one backend
├── data/                    single SQLite file + fastembed model cache
└── run.py, orion.service
```

## 3. Tech stack

- **Backend:** Python + FastAPI + Uvicorn (ASGI). Async `httpx` for all LLM/HTTP.
- **Persistence:** SQLite (single file), stdlib `sqlite3`. `sqlite-vec` extension for vectors
  (in-process, single-file — deliberately **not** a separate ChromaDB service; we can't spare
  the RAM on this box).
- **Local models:** Ollama (`/api/chat`, native). Embeddings via `fastembed` (ONNX, CPU).
- **Frontend:** HTMX-first + minimal vanilla JS. No React, no build step, no Electron, no Docker.
- **Config:** layered JSON in `config/`, editable from the settings UI.
- **Deploy:** `systemd` unit (`Restart=always`), Ollama as a separate systemd service, idle-by-
  default background jobs, log rotation. Remote access via **Tailscale** (no port-forwarding).

## 4. World Model data model (SQLite)

The knowledge lifecycle made concrete. Entity and relationship *types* are extensible by plugins.

| Table | Purpose |
|---|---|
| `entities` | id, type, name, canonical_key, source, timestamps. Types plugin-extensible. |
| `knowledge` | entity_id, key, value, **kind** (`fact`/`observation`/`idea`), **confidence**, source, **status** (`pending`/`accepted`/`rejected`), timestamps. |
| `relationships` | src, dst, type, confidence, status. ("Atlas *originated from* Note #142", 0.83) |
| `events` | the timeline — "what happened, when." Hybrid memory alongside the semantic graph. |
| `workspaces` | the general "bounded area of work" entity (project = trip = learning goal). Starts minimal, plugins enrich. |
| `review_inbox` | the lifecycle gate — nothing inferred becomes truth until Accept/Edit/Reject. |
| `vectors` | `sqlite-vec` KNN over fastembed embeddings — the semantic recall layer (replaces the MVP's `NullRAG` stub). |
| `sessions` / `messages` | conversations, **with `session_id`** (the MVP lacked this). |

**Workspace, not Project.** Orion models *contexts*, not things. "Learn Blender" creates a
Workspace; plugins later enrich it with courses, study sessions, concepts, example repos.

## 5. The immutable reasoning pipeline

Every request flows through the same lifecycle. This pipeline does not change; features plug in.

```
Receive → Understand intent (local) → Consult World Model → Identify gaps
→ Choose cognitive mode → Delegate to specialist(s) → Merge → Update World Model → Respond
```

The LLM is never the first step. The world model is. When knowledge is thin, Orion says so and
proposes to research rather than bluff.

## 6. Plugin contract

A plugin ships a manifest declaring what it contributes; the Plugin Manager discovers and
registers it at startup. A plugin may declare:

```
name · version · specialists · tools · entity_types · relationship_types
· background_jobs · api_routes · dashboard_widgets · settings · permissions · events
```

This is what lets a future Health plugin teach the world model `Workout`/`Meal`/`Sleep`
without touching core.

## 7. Patterns borrowed / carried

**From odysseus (patterns, not breadth):** OpenAI-compatible-first adapters + hostname detection;
visible pre-content fallback chains; foreground/background local-model gate (chat preempts
background jobs on the single CPU/GPU pipe); provider-abstracted memory; embedding "lanes"
(separate vector collections per embedding model to avoid dimensionality corruption); idle-by-
default background tasks + fast-fail service probes; treating all agent-reachable text as
untrusted; a living architecture-inventory doc.

**Carried from the Orion MVP (copied, then cleaned):** provider adapters, tool base classes +
confirm flow, SSE streaming, SQLite store patterns, Gemini rate limiter, dispatcher JSON parsing.
**Fixed on the way in:** API keys → gitignored env (the MVP committed a live key); add
`session_id`; resolve config drift; wire real RAG.

**Explicitly avoided (odysseus's admitted sins):** 4k-line files, flat 90-file packages,
`src → routes` circular-import hacks, 10-provider breadth before the core is solid.

## 8. Security & privacy posture

- No secrets in the repo. API keys live in gitignored env / local config only.
- Local-first by default; data leaves the machine only on an explicit cloud call.
- Human-approval gate for irreversible actions (send email, delete files, spend money).
- Tool execution is local-only; all crawled/agent-reachable text is treated as untrusted.
