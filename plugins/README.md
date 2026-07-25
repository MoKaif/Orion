# Plugins

Everything except the Orion core is a plugin (Manifesto §9). A plugin is a directory here
containing a `manifest.json` and its Python modules. At startup the Plugin Manager
(`orion/core/plugins.py`) discovers each manifest, registers the plugin's contributions, and
mounts its API routes — with failures isolated so one broken plugin never blocks the others.

## Scaffold one

```bash
python -m orion create-plugin health
```

This writes `plugins/health/` with a `manifest.json`, a `register()` hook, and a working example
tool. It loads on next start and appears in `/plugins`, `/tools`, and chat immediately — edit
from a running baseline, not a blank file.

## The contract

A plugin ships two things:

1. **`manifest.json`** — declares what the plugin contributes (used for introspection at
   `/plugins` and to auto-register its world-model types):

   ```json
   {
     "name": "knowledge",
     "version": "0.1.0",
     "specialists": ["research"],
     "tools": ["vault_search", "vault_read"],
     "entity_types": ["concept", "note"],
     "relationship_types": ["mentions", "originated_from"],
     "background_jobs": [{ "name": "index_vault", "cron": "0 * * * *" }],
     "api_routes": [],
     "dashboard_widgets": ["recent_discoveries"],
     "permissions": ["read_vault"]
   }
   ```

   Declared `tools`/`specialists` are checked against what actually registers at load — a
   mismatch logs a warning (declare them by their registered `name`, not the class name).

2. **A `register()` hook** in the package `__init__.py`, built against the **Plugin SDK**
   (`orion.core.plugin_sdk`) — the one stable surface, so plugins never import core registries
   directly:

   ```python
   from orion.core import plugin_sdk as orion

   def register() -> None:
       from .tools import MyTool
       orion.add_tool(MyTool())                                  # RAG-selected, confirm-gated if irreversible
       orion.add_specialist(MySpecialist())                      # domain expert for delegation
       orion.add_entity_type("workout", "A logged exercise session.")   # extends the world model
       orion.add_relationship_type("performed", "User did this workout.")
       orion.add_agent("trainer", "Trainer", tagline="Health", summary=_summary)  # a card on /agents
       orion.add_job("sync_fitbit", "0 * * * *", _sync, agent="trainer",
                     label="Fitbit sync", description="Pull yesterday's activity.",
                     limit_default=10)                           # idle-by-default background job
       orion.add_widget("today_rings", "Today", _render_rings)   # mission-control card (returns HTML)
   ```

### The extension points (Manifesto §9)

| Contribution | How | Where it shows up |
|---|---|---|
| **Tools** | `orion.add_tool(tool)` | RAG tool selection, `/tools`, confirm gate |
| **Specialists** | `orion.add_specialist(s)` | orchestrator delegation, `/specialists` |
| **Agents** | `orion.add_agent(name, title, …)` | a card on `/agents` + its own page, `/api/agents` |
| **Entity / relationship types** | `orion.add_entity_type(...)` / `add_relationship_type(...)`, or just declare them in the manifest | `/types`, graph legend, extract guidance |
| **Background jobs** | `orion.add_job(name, cron, coro, agent=…)` | its agent's page, `/jobs`, retunable per job |
| **Dashboard widgets** | `orion.add_widget(name, title, render)` | mission-control dashboard |
| **Inbox items** | `orion.add_inbox_source(name, fetch)` | the unified inbox at `/inbox` |
| **API routes** | export a module-level `router` (a FastAPI `APIRouter`) | mounted at `/plugins/<name>/…` |

**Anything that waits on the user goes in the inbox.** A source returns normalized dicts, and
every card must answer three things: what it is (`title`, plus `body`/`diff`), where it came
from (`prov_agent`, `prov_label`, optional `prov_uri`), and — the one that matters —
**`effect`: what accepting will actually do**, in the user's own words. Buttons come from
`orion.inbox_action(label, value, tone, confirm=…)`; the label names the outcome ("Discard the
stale copy"), never the mechanism ("accept"), and `confirm` asks for a second click on anything
that deletes. Posting a card's action hits its `action_url` with the button's `value`.

**Agents own jobs.** A job with no `agent=` belongs to the core **Conductor**; naming another
plugin's agent (`agent="curator"`) is safe — if that plugin is disabled the job falls back to the
Conductor rather than vanishing. An agent may pass two callbacks: `summary()` for the headline
numbers on its card (`{"pending": int, "metrics": [{"label", "value"}]}`) and `detail()` for
extra panels on its page (`proposals` / `questions` / `entities` are rendered when present).
Both are called defensively — one that raises never breaks the view. Set `limit_default` on a
batched job to give the user a "per run" dial, and read it at run time with
`orion.job_limit(name, default)`.

Types stay free-text in SQLite (no migrations) — registering a type **documents** it; the store
still accepts any type, honouring "evolution over completion."

## Current plugins

| Plugin | Owns |
|---|---|
| `knowledge` | Obsidian vault ingestion → notes/concepts; semantic index; `recent_discoveries` widget. |
| `software` | `read_file`, `shell`; the software specialist. |
| `research` | `web_search`; the research specialist. |

Later, all as plugins with zero core changes: `operations`, `calendar`, `finance`, `health`.
