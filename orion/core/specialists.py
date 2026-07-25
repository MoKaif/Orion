"""Specialists — domain experts the orchestrator delegates to.

A specialist doesn't replace the pipeline; it *shapes a turn*: it contributes domain guidance
(a system-prompt fragment) and a preferred tool set, and scores how well it fits a message. The
orchestrator picks the best-scoring specialist (falling back to the Generalist) after consulting
the world model. Specialists are registered by plugins — the core ships only the Generalist.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Specialist(ABC):
    name: str = "specialist"
    description: str = ""
    keywords: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()          # tool names this specialist prefers

    def score(self, message: str) -> float:
        if not self.keywords:
            return 0.0
        m = message.lower()
        return sum(k in m for k in self.keywords) / len(self.keywords)

    @abstractmethod
    def system_fragment(self) -> str:
        """Domain guidance appended to the system prompt for this turn."""
        ...


class Generalist(Specialist):
    name = "generalist"
    description = "Default assistant; handles anything without a better-fit specialist."

    def system_fragment(self) -> str:
        return ""


_SPECIALISTS: dict[str, Specialist] = {}


def register(specialist: Specialist) -> None:
    _SPECIALISTS[specialist.name] = specialist


def all_specialists() -> list[Specialist]:
    return list(_SPECIALISTS.values())


def select(message: str, threshold: float = 0.01) -> Specialist:
    """Highest-scoring specialist, or the Generalist when none fits."""
    best, best_score = _GENERALIST, 0.0
    for s in _SPECIALISTS.values():
        score = s.score(message)
        if score > best_score:
            best, best_score = s, score
    return best if best_score > threshold else _GENERALIST


_GENERALIST = Generalist()
register(_GENERALIST)
