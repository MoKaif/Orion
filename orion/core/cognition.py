"""Cognitive-mode selection: how much thinking does a task deserve?

Orion — not the user — picks the mode. The mode maps to a model tier in the router, which is
how cost is controlled: reflex work stays local (free); only reasoning/deep-work reach cloud.

M0: heuristic classifier (keyword + length). M2: replace the body of ``classify`` with a
local-model call, keeping this interface stable.
"""
from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    REFLEX = "reflex"        # ms -> sec : answer, retrieve, search, simple classify   -> local
    REASONING = "reasoning"  # 10s -> 2m : design, review, plan, summarize, compare    -> cloud
    DEEP_WORK = "deep_work"  # min -> hr : create project, research, analyze graph      -> cloud (metered)


_DEEP = ("research", "build a", "create a project", "refactor", "analyze", "roadmap", "read the")
_REASON = ("design", "review", "plan", "summarize", "compare", "explain the tradeoff")


def classify(message: str) -> Mode:
    """Heuristic mode selection (placeholder for the M2 local classifier)."""
    m = message.lower()
    if any(k in m for k in _DEEP) or len(message) > 600:
        return Mode.DEEP_WORK
    if any(k in m for k in _REASON):
        return Mode.REASONING
    return Mode.REFLEX
