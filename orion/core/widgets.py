"""Plugin dashboard widgets.

A widget is a small, server-rendered card a plugin contributes to mission control without
touching the dashboard template. A plugin registers a name, a title, and a ``render`` callable
that returns an HTML fragment string; the dashboard includes each registered widget in its
"From your plugins" strip.

Rendering is isolated: a widget that raises is skipped (never takes the dashboard down), same
graceful-degradation rule the rest of the core follows. Widget HTML is authored by the plugin,
which is trusted code in this single-user system — it is inserted as-is.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("orion.widgets")


@dataclass
class Widget:
    name: str
    title: str
    render: Callable[[], str]
    plugin: str = "core"


_WIDGETS: dict[str, Widget] = {}


def register(widget: Widget) -> None:
    _WIDGETS[widget.name] = widget


def all_widgets() -> list[Widget]:
    return list(_WIDGETS.values())


def render_all() -> list[dict]:
    """[{name, title, html}] for the dashboard; a failing widget is skipped, not fatal."""
    out: list[dict] = []
    for w in _WIDGETS.values():
        try:
            html = w.render()
        except Exception as e:  # isolation: one bad widget never breaks the dashboard
            log.warning("widget '%s' failed to render: %s", w.name, e)
            continue
        out.append({"name": w.name, "title": w.title, "html": html})
    return out
