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
       orion.add_job("sync_fitbit", "0 * * * *", _sync)          # idle-by-default background job
       orion.add_widget("today_rings", "Today", _render_rings)   # mission-control card (returns HTML)
   ```

### The six extension points (Manifesto §9)

| Contribution | How | Where it shows up |
|---|---|---|
| **Tools** | `orion.add_tool(tool)` | RAG tool selection, `/tools`, confirm gate |
| **Specialists** | `orion.add_specialist(s)` | orchestrator delegation, `/specialists` |
| **Entity / relationship types** | `orion.add_entity_type(...)` / `add_relationship_type(...)`, or just declare them in the manifest | `/types`, graph legend, extract guidance |
| **Background jobs** | `orion.add_job(name, cron, coro)` | scheduler, `/jobs`, retunable in `config/jobs.json` |
| **Dashboard widgets** | `orion.add_widget(name, title, render)` | mission-control dashboard |
| **API routes** | export a module-level `router` (a FastAPI `APIRouter`) | mounted at `/plugins/<name>/…` |

Types stay free-text in SQLite (no migrations) — registering a type **documents** it; the store
still accepts any type, honouring "evolution over completion."

## Current plugins

| Plugin | Owns |
|---|---|
| `knowledge` | Obsidian vault ingestion → notes/concepts; semantic index; `recent_discoveries` widget. |
| `software` | `read_file`, `shell`; the software specialist. |
| `research` | `web_search`; the research specialist. |

Later, all as plugins with zero core changes: `operations`, `calendar`, `finance`, `health`.
