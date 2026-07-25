"""Orion command line — ``python -m orion <command>``.

Today it scaffolds plugins; the platform being "complete" means new capability arrives as a
plugin, and this is the fast path to a well-formed one:

    python -m orion create-plugin health

Every generated plugin already wires a working tool through the SDK, so it loads and appears in
``/plugins`` / ``/tools`` immediately — edit from a running baseline instead of a blank file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _manifest(name: str) -> str:
    return json.dumps({
        "name": name,
        "version": "0.1.0",
        "specialists": [],
        "tools": [f"{name}_hello"],
        "entity_types": [],
        "relationship_types": [],
        "background_jobs": [],
        "api_routes": [],
        "dashboard_widgets": [],
        "permissions": [],
    }, indent=2) + "\n"


def _init_py(name: str) -> str:
    return f'''"""{name.capitalize()} plugin — scaffolded by `python -m orion create-plugin {name}`.

Wire tools / specialists / jobs / entity types / widgets here through the SDK, then declare
them in manifest.json so mission control can introspect them. Export a module-level `router`
(a FastAPI APIRouter) to add API routes under /plugins/{name}. See plugins/README.md.
"""
from orion.core import plugin_sdk as orion


def register() -> None:
    from .tools import HelloTool
    orion.add_tool(HelloTool())
'''


def _tools_py(name: str) -> str:
    return f'''"""Tools for the {name} plugin."""
from orion.core.plugin_sdk import BaseTool, ToolResult


class HelloTool(BaseTool):
    name = "{name}_hello"
    description = "Example tool scaffolded for the {name} plugin. Replace with real behaviour."
    triggers = ("{name} hello",)

    async def run(self, args: dict) -> ToolResult:
        return ToolResult(ok=True, output="Hello from the {name} plugin.")
'''


def create_plugin(name: str) -> int:
    if not _NAME_RE.match(name):
        print(f"error: invalid plugin name '{name}' — use lowercase letters, digits, underscores;"
              " must start with a letter.", file=sys.stderr)
        return 2
    plugin_dir = _PLUGINS_DIR / name
    if plugin_dir.exists():
        print(f"error: plugins/{name} already exists.", file=sys.stderr)
        return 1
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.json").write_text(_manifest(name))
    (plugin_dir / "__init__.py").write_text(_init_py(name))
    (plugin_dir / "tools.py").write_text(_tools_py(name))
    print(f"created plugins/{name}/  (manifest.json, __init__.py, tools.py)")
    print(f"it loads on next start — try `{name} hello` in chat, or GET /plugins.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orion", description="Orion platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    cp = sub.add_parser("create-plugin", help="scaffold a new plugin under plugins/")
    cp.add_argument("name", help="plugin name (lowercase identifier, e.g. health)")
    args = parser.parse_args(argv)
    if args.command == "create-plugin":
        return create_plugin(args.name)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
