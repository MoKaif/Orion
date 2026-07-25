"""The unified inbox — one queue for everything that is waiting on the user.

The world model's own review queue is always in it. Anything else arrives through a registered
**source**: a plugin hands over a callable returning already-normalized items, so core no longer
imports Curator to build the inbox (it used to, which is the same coupling the agent registry
removed from the Agents view).

Every item, whatever its origin, must answer three questions on the card:

    what is this?          -> `title` + `body`/`diff`
    where did it come from? -> `prov_agent` + `prov_label` (+ `prov_uri` to open it)
    what happens if I say yes? -> `effect`, and `actions` naming the outcomes in the user's words

`effect` is the field that was missing: the old inbox rendered a duplicate notice as an empty
"record this observation about undefined" card, and accepting it silently did nothing at all.
Sources that can't describe an effect should say so plainly rather than leave it blank.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("orion.inbox")


@dataclass
class InboxSource:
    name: str
    fetch: Callable[[], list[dict[str, Any]]]
    plugin: str = "core"


_SOURCES: dict[str, InboxSource] = {}


def register(source: InboxSource) -> None:
    _SOURCES[source.name] = source


def sources() -> list[InboxSource]:
    return list(_SOURCES.values())


def items() -> list[dict[str, Any]]:
    """Every registered source's items. A source that raises is skipped, never fatal."""
    out: list[dict[str, Any]] = []
    for source in _SOURCES.values():
        try:
            out.extend(source.fetch() or [])
        except Exception as e:
            log.warning("inbox source '%s' failed: %s", source.name, e)
    return out


def action(label: str, value: str, tone: str = "neutral", *,
           confirm: str | None = None) -> dict[str, Any]:
    """One button on a card. ``label`` is what the user reads and must name the outcome.

    tone: ``accept`` (does the thing) · ``reject`` (declines it) · ``neutral``.
    ``confirm`` asks for a second click, for actions that delete something.
    """
    return {"label": label, "value": value, "tone": tone, "confirm": confirm}
