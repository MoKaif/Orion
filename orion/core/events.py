"""In-process asynchronous event bus — Orion's event-driven spine.

Orion is event-driven, not request-driven: interfaces, the scheduler, and plugins publish
events (new note, git commit, message received, knowledge inferred); subscribers react and
may update the world model. Kept in-process and dependency-free by design (modular monolith).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("orion.events")

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "core"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        """Dispatch to all subscribers concurrently; one failure never blocks the others."""
        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                log.warning("event handler failed for %s: %r", event.type, r)


bus = EventBus()
