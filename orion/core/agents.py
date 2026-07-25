"""Agents — the named workers the user sees, each owning a set of background jobs.

Mission control used to group jobs by a hardcoded map in the API layer, which meant core had to
know Curator's job names: exactly the coupling the plugin SDK exists to prevent. An agent is now
a first-class registration. A plugin declares who the agent is (title, tagline, blurb, accent),
its jobs point at it by name, and it may contribute two callbacks:

    summary()  -> headline numbers for the agent's card and page
    detail()   -> extra panels for its page (proposals, questions, registry, ...)

Both are optional and isolated — an agent whose callback raises still renders, same
graceful-degradation rule as widgets. Adding a third agent is one ``add_agent`` call in a
plugin; core does not change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("orion.agents")

#: Jobs that don't name an agent belong to the core Conductor.
DEFAULT_AGENT = "conductor"

#: Accents an agent may claim, mapped to design tokens in the UI. Keeping the set closed is
#: what stops a new plugin from inventing a colour that clashes with the knowledge kinds.
ACCENTS = ("copper", "fact", "observation", "idea")


@dataclass
class Agent:
    name: str                       # url-safe id, matches ScheduledJob.agent
    title: str                      # display name ("Curator")
    tagline: str = ""               # eyebrow above the title ("Obsidian vault")
    blurb: str = ""                 # what it does, and what it won't do without you
    icon: str = "bot"               # lucide icon name; the UI maps it to a component
    accent: str = "copper"
    plugin: str = "core"
    order: int = 100                # display order on the Agents view
    summary: Callable[[], dict[str, Any]] | None = field(default=None, repr=False)
    detail: Callable[[], dict[str, Any]] | None = field(default=None, repr=False)

    def card(self) -> dict[str, Any]:
        """The identity half of an API payload (no live numbers, no jobs)."""
        return {"name": self.name, "title": self.title, "tagline": self.tagline,
                "blurb": self.blurb, "icon": self.icon,
                "accent": self.accent if self.accent in ACCENTS else "copper",
                "plugin": self.plugin}


_AGENTS: dict[str, Agent] = {}


def register(agent: Agent) -> None:
    if agent.accent not in ACCENTS:
        log.warning("agent '%s' claims unknown accent '%s'; using copper",
                    agent.name, agent.accent)
    _AGENTS[agent.name] = agent


def get(name: str) -> Agent | None:
    return _AGENTS.get(name)


def all_agents() -> list[Agent]:
    return sorted(_AGENTS.values(), key=lambda a: (a.order, a.title))


_warned: set[str] = set()


def resolve(name: str | None) -> Agent:
    """The named agent, or the Conductor when a job names one that isn't registered.

    Keeps a plugin free to hand its job to another plugin's agent (``agent="curator"``)
    without a hard dependency: if that plugin is disabled, the job still has a home. Called
    on every agents request, so the fallback warns once per name rather than per request.
    """
    agent = _AGENTS.get(name or "")
    if agent is not None:
        return agent
    if name and name not in _warned:
        _warned.add(name)
        log.warning("job claims unregistered agent '%s'; filing under '%s'", name, DEFAULT_AGENT)
    return _AGENTS.get(DEFAULT_AGENT) or Agent(DEFAULT_AGENT, "Conductor")


def summary_of(agent: Agent) -> dict[str, Any]:
    """``agent.summary()``, isolated. Shape: {"pending": int, "metrics": [{label, value}]}."""
    return _safe(agent, agent.summary, "summary")


def detail_of(agent: Agent) -> dict[str, Any]:
    """``agent.detail()``, isolated. Extra panels the agent's page knows how to render."""
    return _safe(agent, agent.detail, "detail")


def _safe(agent: Agent, fn: Callable[[], dict[str, Any]] | None, what: str) -> dict[str, Any]:
    if fn is None:
        return {}
    try:
        return fn() or {}
    except Exception as e:                      # one bad agent never breaks the view
        log.warning("agent '%s' %s() failed: %s", agent.name, what, e)
        return {}
