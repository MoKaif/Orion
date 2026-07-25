"""Plugin manager — everything except the core is a plugin.

At startup, discovers plugins under ``plugins/``, reads each manifest, and registers what it
contributes: specialists, tools, entity/relationship types (extending the world model),
background jobs, API routes, dashboard widgets. This is what lets a future Health plugin teach
the world model Workout/Meal/Sleep without touching core.

M0: manifest model + discovery skeleton. M2+: actual registration wiring and the
``orion create-plugin`` scaffolder.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("orion.plugins")
_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    specialists: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    relationship_types: list[str] = field(default_factory=list)
    background_jobs: list[dict[str, Any]] = field(default_factory=list)
    api_routes: list[str] = field(default_factory=list)
    dashboard_widgets: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)


def discover() -> list[PluginManifest]:
    """Find plugins/*/manifest.json."""
    found: list[PluginManifest] = []
    if not _PLUGINS_DIR.exists():
        return found
    for manifest_path in sorted(_PLUGINS_DIR.glob("*/manifest.json")):
        try:
            data = {k: v for k, v in json.loads(manifest_path.read_text()).items()
                    if not k.startswith("//")}
            found.append(PluginManifest(**data))
        except (json.JSONDecodeError, TypeError, OSError) as e:
            log.warning("bad manifest %s: %s", manifest_path, e)
    return found


def load_all(app: object | None = None) -> list[str]:
    """Discover, import, and fully wire every plugin. Returns the names that loaded.

    For each plugin this: (1) auto-registers the entity/relationship *types* its manifest
    declares (so declarations are live in the type registry even if ``register()`` doesn't
    call the SDK), (2) calls its ``register()`` hook — which wires tools, specialists, jobs,
    widgets via ``orion.core.plugin_sdk``, (3) mounts a module-level ``router`` (a FastAPI
    APIRouter) under ``/plugins/<name>`` when an ``app`` is passed, and (4) checks the manifest
    against what actually registered, warning on drift. Failures are isolated: one broken
    plugin never blocks the others — that is what makes 'everything except core is a plugin' safe.
    """
    import importlib

    from orion.core.world_model import types as type_registry

    loaded: list[str] = []
    for manifest in discover():
        try:
            # (1) declared types → registry (additive; store still accepts any type)
            for name in manifest.entity_types:
                type_registry.register_entity_type(name, plugin=manifest.name)
            for name in manifest.relationship_types:
                type_registry.register_relationship_type(name, plugin=manifest.name)

            # (2) run the plugin's register() hook
            module = importlib.import_module(f"plugins.{manifest.name}")
            hook = getattr(module, "register", None)
            if callable(hook):
                hook()

            # (3) mount an optional plugin API router under /plugins/<name>
            router = getattr(module, "router", None)
            if router is not None and app is not None:
                app.include_router(router, prefix=f"/plugins/{manifest.name}",
                                   tags=[manifest.name])

            _check_manifest(manifest)
            loaded.append(manifest.name)
        except Exception as e:
            log.warning("plugin '%s' failed to load: %s", manifest.name, e)
    return loaded


def _check_manifest(manifest: PluginManifest) -> None:
    """Warn when a manifest declares tools/specialists that never actually registered."""
    from orion.core import specialists
    from orion.core.tools import registry as tools

    have_tools = {t.name for t in tools.all_tools()}
    for name in manifest.tools:
        if name not in have_tools:
            log.warning("plugin '%s' declares tool '%s' but it did not register",
                        manifest.name, name)
    have_specialists = {s.name for s in specialists.all_specialists()}
    for name in manifest.specialists:
        if name not in have_specialists:
            log.warning("plugin '%s' declares specialist '%s' but it did not register",
                        manifest.name, name)
