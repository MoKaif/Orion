"""Core built-in tools — the tiny always-available set (see config tools.selection).

These ship with core (not a plugin) because the orchestrator relies on them existing:
- manage_memory: record something durable into the world model (via the review lifecycle).
- ask_user: signal that Orion needs a clarification before it can proceed.
"""
from __future__ import annotations

from typing import Any

from orion.core.world_model import world_model
from .base import BaseTool, ToolResult
from . import registry


class ManageMemoryTool(BaseTool):
    name = "manage_memory"
    description = "Record a durable fact/observation/preference about the user into the world model."
    triggers = ("remember", "note that", "keep in mind", "don't forget")
    args_schema = {"entity": "str", "value": "str", "kind": "fact|observation|idea",
                   "confidence": "float 0-1"}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        if not args.get("value"):
            return ToolResult(False, "nothing to remember (missing 'value')")
        candidate = {
            "entity": args.get("entity", "user"),
            "entity_type": "preference",
            "key": args.get("key", "note"),
            "value": args["value"],
            "kind": args.get("kind", "observation"),
            "confidence": float(args.get("confidence", 0.9)),
            "source": "manage_memory",
        }
        outcome = world_model.ingest_candidate(candidate)
        return ToolResult(True, f"recorded ({outcome['outcome']})", meta=outcome)


class AskUserTool(BaseTool):
    name = "ask_user"
    description = "Ask the user a clarifying question when the request is ambiguous."
    triggers = ()
    args_schema = {"question": "str"}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        q = args.get("question", "Could you clarify?")
        return ToolResult(True, q, meta={"needs_user_input": True})


def register_builtins() -> None:
    registry.register(ManageMemoryTool())
    registry.register(AskUserTool())
