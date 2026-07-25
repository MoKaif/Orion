"""Knowledge plugin tools — search and read the world model / vault (local, no network)."""
from __future__ import annotations

from typing import Any

from orion.core.tools.base import BaseTool, ToolResult
from orion.core.world_model import world_model


class VaultSearchTool(BaseTool):
    name = "vault_search"
    description = "Search the user's knowledge (notes, facts) by topic and return the best matches."
    triggers = ("search my notes", "find in vault", "search vault", "what do i know about",
                "look up", "search my knowledge")
    args_schema = {"query": "str"}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(False, "no query provided")
        hits = world_model.recall(query, limit=8)
        if not hits:
            return ToolResult(True, f"No knowledge found for '{query}'.")
        lines = [f"- [{h['type']}] {h['name']}: {h['value'][:200]}" for h in hits]
        return ToolResult(True, f"Knowledge for '{query}':\n" + "\n".join(lines),
                          meta={"count": len(hits)})


class VaultReadTool(BaseTool):
    name = "vault_read"
    description = "Read the full stored content of a specific note by its title."
    triggers = ("read note", "open note", "show me the note")
    args_schema = {"title": "str"}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        title = args.get("title", "").strip()
        hits = world_model.recall(title, limit=1)
        if not hits:
            return ToolResult(True, f"No note titled '{title}'.")
        return ToolResult(True, hits[0]["value"], meta={"name": hits[0]["name"]})
