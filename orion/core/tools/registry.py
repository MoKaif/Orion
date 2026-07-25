"""Tool registry. Plugins register tools here at load time.

M0: registration + enable-flag filtering + trigger match (scored by hit count, longest
trigger — carried from the MVP). M2 adds RAG-based selection: embed tool descriptions and
retrieve only top-K per message, plus a tiny always-available set, so small-model prompts
stay cheap.
"""
from __future__ import annotations

from orion.core.config import config
from .base import BaseTool

_TOOLS: dict[str, BaseTool] = {}


def register(tool: BaseTool) -> None:
    _TOOLS[tool.name] = tool


def get(name: str) -> BaseTool | None:
    tool = _TOOLS.get(name)
    return tool if tool and _is_enabled(tool) else None


def all_tools() -> list[BaseTool]:
    return [t for t in _TOOLS.values() if _is_enabled(t)]


def _is_enabled(tool: BaseTool) -> bool:
    flags = config.section("tools").get("enabled", {})
    prefix = tool.name.split("_")[0]
    return flags.get(prefix, flags.get(tool.name, True))


def match(message: str) -> BaseTool | None:
    m = message.lower()
    best, best_score = None, (0, 0)
    for tool in all_tools():
        hits = [t for t in tool.triggers if t in m]
        if not hits:
            continue
        score = (len(hits), max(len(t) for t in hits))
        if score > best_score:
            best, best_score = tool, score
    return best
