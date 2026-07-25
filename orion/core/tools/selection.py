"""RAG-based tool selection — the key pattern for keeping small-model prompts affordable.

Instead of injecting every tool schema into every prompt (fatal for 4k-context local models),
embed tool descriptions once and retrieve only the top-K relevant tools per message, unioned
with a tiny always-available set and the active specialist's preferred tools. Borrowed from
odysseus.

Degrades to a keyword scorer when the vector index isn't available.
"""
from __future__ import annotations

import re

from orion.core.config import config
from orion.core.world_model.vectors import vectors
from . import registry
from .base import BaseTool

_LANE = "tools"


def index_tools() -> int:
    """Embed each enabled tool's description into the 'tools' vector lane. No-op if vectors off."""
    if not vectors.is_available():
        return 0
    n = 0
    for tool in registry.all_tools():
        text = f"{tool.name}: {tool.description}. triggers: {' '.join(tool.triggers)}"
        vectors.add(f"tool:{tool.name}", text, lane=_LANE)
        n += 1
    return n


def _keyword_rank(message: str, tools: list[BaseTool], k: int) -> list[str]:
    terms = [t for t in re.findall(r"\w+", message.lower()) if len(t) >= 3]
    scored = []
    for tool in tools:
        hay = f"{tool.name} {tool.description} {' '.join(tool.triggers)}".lower()
        score = sum(t in hay for t in terms)
        if score:
            scored.append((score, tool.name))
    scored.sort(reverse=True)
    return [name for _, name in scored[:k]]


def select_tools(message: str, specialist=None) -> list[BaseTool]:
    cfg = config.section("tools").get("selection", {})
    k = cfg.get("top_k", 5)
    enabled = registry.all_tools()
    names = {t.name for t in enabled}

    # always-available core set
    picked: list[str] = [n for n in cfg.get("always_available", []) if n in names]

    # top-K by relevance (semantic when possible, else keyword)
    if vectors.is_available():
        for hit in vectors.search(message, k=k, lane=_LANE):
            ref = hit["ref"]
            if ref.startswith("tool:") and ref[5:] in names:
                picked.append(ref[5:])
    else:
        picked += _keyword_rank(message, enabled, k)

    # the active specialist's preferred tools
    if specialist is not None:
        picked += [t for t in getattr(specialist, "tools", ()) if t in names]

    seen, ordered = set(), []
    for n in picked:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return [registry.get(n) for n in ordered if registry.get(n)]
