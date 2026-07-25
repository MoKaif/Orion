# Orion

A single-user **Personal Knowledge Operating System** — a continuously running cognitive
system that maintains a living *world model* of its user's digital life and assists proactively
across every domain, not just software.

This is a fresh rewrite targeting an always-on **8GB CPU-only Arch Linux** host. Local-first by
default; cloud LLMs are a cost-metered fallback.

## Documents (read these first)

- [`docs/ORION_MANIFESTO.md`](docs/ORION_MANIFESTO.md) — vision, constitution, first principles.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — technical design, model/cost strategy, data model.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — milestones and exit criteria.
- [`docs/VERSIONING.md`](docs/VERSIONING.md) — how releases are cut; [`CHANGELOG.md`](CHANGELOG.md).

## Status — `0.6.0`, M0–M5 complete

**The platform is finished; new capability is now a new plugin with zero core changes.**

- **M1** world model — entities, knowledge lifecycle (fact/observation/idea + confidence +
  status), relationships, events, workspaces, the review inbox, hybrid semantic+keyword recall,
  Obsidian ingestion, chat that consults the world model before answering.
- **M2** orchestrator — cognitive-mode selection, best-fit specialist delegation, RAG tool
  selection, confirm-gated tool execution.
- **M3** background lifecycle — idle-by-default scheduler, proactive review generation,
  foreground/background gate so chat preempts jobs.
- **M4** mission control — the "Obsidian Archive" HTMX web UI: dashboard, telemetry rail, SSE
  streaming chat surfacing mode/specialist/tool runs, sessions view, ⌘K palette, light theme.
- **M5** plugin SDK — one stable `register()` surface covering tools, specialists, world-model
  types, jobs, dashboard widgets, and a mounted `/plugins/<name>` router, plus a scaffolder.
- **Curator v2** — the vault's resident editor: nightly review-gated grammar, entity-registry,
  backlink, and journal→world-model passes, with backups before any write.

Runs as a Docker service on `:8020`. Chat routes to DeepSeek `deepseek-v4-flash`; background
dispatch/compress/embed stay local on Ollama + fastembed. The on-host smoke test passes
(`scripts/smoke_test.py`): live fastembed + sqlite-vec recall, a real Ollama turn, and a full
end-to-end pipeline run.

Next: content plugins (operations / finance / health / calendar) and a Pi/voice client.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py            # serves http://127.0.0.1:8000  (GET /health)
```

Requires [Ollama](https://ollama.com) on `localhost:11434` for local reflex tasks.
Cloud fallback keys go in `.env` / `config/secrets.json` (both gitignored) — **never** in
tracked config.

## Layout

```
orion/core/     small core: config, events, world_model, cognition, orchestrator,
                providers, tools, scheduler, plugins, identity, api
plugins/        everything else (knowledge, software, research, ...) — see plugins/README.md
interfaces/web/ HTMX dashboard + chat
config/         layered JSON config
data/           SQLite DB + local model cache (gitignored)
```
