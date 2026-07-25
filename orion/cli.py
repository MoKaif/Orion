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
        "agents": [name],
        "tools": [f"{name}_hello"],
        "entity_types": [],
        "relationship_types": [],
        "background_jobs": [{"name": f"{name}_sweep", "cron": "0 5 * * *"}],
        "api_routes": [],
        "dashboard_widgets": [],
        "permissions": [],
    }, indent=2) + "\n"


def _init_py(name: str) -> str:
    title = name.capitalize()
    return f'''"""{title} plugin — scaffolded by `python -m orion create-plugin {name}`.

Wire tools / specialists / agents / jobs / entity types / widgets here through the SDK, then
declare them in manifest.json so mission control can introspect them. Export a module-level
`router` (a FastAPI APIRouter) to add API routes under /plugins/{name}. See plugins/README.md.
"""
from orion.core import plugin_sdk as orion


def register() -> None:
    from .tools import HelloTool
    orion.add_tool(HelloTool())

    # An agent is what the user sees on the Agents view: a named worker owning the passes
    # below. Give it a real blurb — it's the first thing read about this plugin.
    orion.add_agent(
        "{name}", "{title}",
        tagline="{title}",
        blurb="What {title} looks after, and what it will never change without asking you.",
        icon="bot", accent="idea", plugin="{name}", order=100, summary=_summary)

    orion.add_job("{name}_sweep", "0 5 * * *", _sweep, agent="{name}",
                  label="Daily sweep",
                  description="Replace with what one run of this pass actually does.",
                  limit_default=10)


async def _sweep() -> dict:
    """One run of the pass. Keep it bounded — `orion.job_limit` is the user's dial for that."""
    limit = orion.job_limit("{name}_sweep", 10)
    return {{"ok": True, "checked": 0, "limit": limit}}


def _summary() -> dict:
    """Headline numbers for this agent's card. Called defensively — may return {{}}."""
    return {{"pending": 0, "metrics": [{{"label": "nothing yet", "value": 0}}]}}
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
    print(f"it loads on next start — try `{name} hello` in chat, see GET /plugins,")
    print(f"and find its agent card at /agents/{name}.")
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
