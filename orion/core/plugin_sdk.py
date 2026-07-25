"""The Orion Plugin SDK — the stable surface a plugin's ``register()`` builds against.

Manifesto §9: "everything except core is a plugin." A plugin ships ``manifest.json`` + a
``register()`` hook and, through this one module, can contribute every extension point the
manifesto promises — **without importing core internals**:

    from orion.core import plugin_sdk as orion

    def register():
        orion.add_tool(MyTool())
        orion.add_specialist(MySpecialist())
        orion.add_entity_type("workout", "A logged exercise session.")
        orion.add_relationship_type("performed", "User did this workout.")
        orion.add_job("sync_fitbit", "0 * * * *", _sync)
        orion.add_widget("today_rings", "Today", _render_rings)
        # API routes: export a module-level `router` (a FastAPI APIRouter); the loader
        # mounts it under /plugins/<name> automatically.

Keeping this facade thin and stable is what lets plugins outlive refactors of the core
registries they target.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from orion.core import specialists, widgets
from orion.core.scheduler import ScheduledJob, configured_cron, scheduler
from orion.core.tools import registry as _tools
from orion.core.tools.base import BaseTool, ToolResult
from orion.core.world_model import types as _types

__all__ = [
    "add_tool", "add_specialist", "add_job",
    "add_entity_type", "add_relationship_type", "add_widget",
    "Specialist", "BaseTool", "ToolResult", "Widget",
]

Specialist = specialists.Specialist
Widget = widgets.Widget


def add_tool(tool: BaseTool) -> None:
    """Register a tool the orchestrator can RAG-select and run (confirm-gated if irreversible)."""
    _tools.register(tool)


def add_specialist(specialist: specialists.Specialist) -> None:
    """Register a domain specialist the orchestrator may delegate a turn to."""
    specialists.register(specialist)


def add_job(name: str, cron_default: str, run: Callable[[], Awaitable[object]],
            preemptible: bool = True) -> None:
    """Register an idle-by-default background job. The user can retune its cron in jobs.json."""
    scheduler.register(ScheduledJob(name, configured_cron(name, cron_default), run,
                                    foreground_preemptible=preemptible))


def add_entity_type(name: str, description: str = "", color: str | None = None,
                    plugin: str = "core") -> None:
    """Document a new entity type this plugin introduces (extends the world model)."""
    _types.register_entity_type(name, description, color, plugin)


def add_relationship_type(name: str, description: str = "", color: str | None = None,
                          plugin: str = "core") -> None:
    """Document a new relationship type this plugin introduces."""
    _types.register_relationship_type(name, description, color, plugin)


def add_widget(name: str, title: str, render: Callable[[], str], plugin: str = "core") -> None:
    """Register a server-rendered mission-control card (returns an HTML fragment string)."""
    widgets.register(widgets.Widget(name=name, title=title, render=render, plugin=plugin))
