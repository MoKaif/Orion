"""Cross-agent reporting — how one plugin's news reaches another plugin's letter.

Herald composes its letters from things it can read directly: the world model's stats, the
unified inbox, the scheduler's history, the router's ledger. That covers everything *core*
knows, but not what a plugin knows about its own work. Maintainer opening three pull requests
overnight is exactly the kind of news a morning briefing exists to carry, and Herald must not
have to import Maintainer to carry it (plugins never import each other — that is the coupling
the agent and inbox registries were built to remove).

So a plugin registers a **report source** here, and Herald folds in whatever is registered:

    sections(scope)  -> extra letter sections, same dicts render.py already understands
    facts(scope)     -> extra keys in the JSON block the prose model is given, so the lede
                        can *mention* the news instead of it appearing only as figures
    alerts()         -> problems worth mailing about, in watch_alerts' own shape

``scope`` is the letter being written ("briefing" · "weekly"), so one callable can be terse
daily and thorough weekly. Every callable is optional and every call is defensive: a source
that raises is skipped and the letter still goes out. That is the same rule as ``inbox.items``
— a broken contributor must never cost the user their briefing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("orion.reports")


@dataclass
class ReportSource:
    name: str
    sections: Callable[[str], list[dict[str, Any]]] | None = None
    facts: Callable[[str], dict[str, Any]] | None = None
    alerts: Callable[[], list[dict[str, Any]]] | None = None
    plugin: str = "core"


_SOURCES: dict[str, ReportSource] = {}


def register(source: ReportSource) -> None:
    _SOURCES[source.name] = source


def sources() -> list[ReportSource]:
    return list(_SOURCES.values())


def _gather(attr: str, *args) -> list[Any]:
    """Call one hook on every source, skipping (and logging) any that fails."""
    out: list[Any] = []
    for source in _SOURCES.values():
        hook = getattr(source, attr)
        if hook is None:
            continue
        try:
            result = hook(*args)
        except Exception as e:
            log.warning("report source '%s' failed on %s: %s", source.name, attr, e)
            continue
        if result:
            out.append(result)
    return out


def sections(scope: str = "briefing") -> list[dict[str, Any]]:
    """Extra sections for this letter, in registration order."""
    out: list[dict[str, Any]] = []
    for block in _gather("sections", scope):
        out.extend(block)
    return out


def facts(scope: str = "briefing") -> dict[str, Any]:
    """Extra facts for the prose model. Later sources win on a key collision."""
    out: dict[str, Any] = {}
    for block in _gather("facts", scope):
        if isinstance(block, dict):
            out.update(block)
    return out


def alerts() -> list[dict[str, Any]]:
    """Problems worth an alert mail.

    Each item is ``{key, heading, detail, when}`` — the shape Herald's ``watch_alerts``
    already fires — plus an optional ``resolved: True``, which means "this problem went away,
    forget the cooldown so the next occurrence alerts immediately".
    """
    out: list[dict[str, Any]] = []
    for block in _gather("alerts"):
        out.extend(block)
    return out
