"""Plugin-extensible entity & relationship *type* registry.

Types stay free-text in SQLite (schema.py keeps no CHECK on entity/relationship ``type`` —
so plugins add kinds without migrations). This registry is the **discovery layer** on top of
that: a plugin declares the types it introduces, with a human description and an optional UI
colour, so the graph legend, extract guidance, and ``/types`` know what exists in the world.

Registration is additive and never *rejects* an unknown type at write time (Manifesto §2.8,
"evolution over completion"): the store still accepts any type; the registry just describes the
ones a plugin has chosen to document. Declaring a type twice keeps the first non-empty
description.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TypeSpec:
    name: str
    kind: str            # "entity" | "relationship"
    description: str = ""
    color: str | None = None
    plugin: str = "core"


_TYPES: dict[tuple[str, str], TypeSpec] = {}


def _register(spec: TypeSpec) -> None:
    key = (spec.kind, spec.name)
    existing = _TYPES.get(key)
    # Keep the first documented description if a later declaration is bare.
    if existing and existing.description and not spec.description:
        return
    _TYPES[key] = spec


def register_entity_type(name: str, description: str = "", color: str | None = None,
                         plugin: str = "core") -> None:
    _register(TypeSpec(name=name, kind="entity", description=description,
                       color=color, plugin=plugin))


def register_relationship_type(name: str, description: str = "", color: str | None = None,
                               plugin: str = "core") -> None:
    _register(TypeSpec(name=name, kind="relationship", description=description,
                       color=color, plugin=plugin))


def entity_types() -> list[TypeSpec]:
    return sorted((s for s in _TYPES.values() if s.kind == "entity"), key=lambda s: s.name)


def relationship_types() -> list[TypeSpec]:
    return sorted((s for s in _TYPES.values() if s.kind == "relationship"), key=lambda s: s.name)


def all_types() -> dict[str, list[dict]]:
    """Serialisable view for the /types endpoint and the graph legend."""
    def dump(specs: list[TypeSpec]) -> list[dict]:
        return [{"name": s.name, "description": s.description,
                 "color": s.color, "plugin": s.plugin} for s in specs]
    return {"entity": dump(entity_types()), "relationship": dump(relationship_types())}


# -- core seed --------------------------------------------------------------
# The handful of types the core world model uses out of the box; plugins add the rest.
for _n, _d in (("person", "A human — the user, a collaborator, an author."),
               ("project", "A bounded body of work (also a workspace)."),
               ("concept", "An idea, topic, or thing being reasoned about."),
               ("workspace", "An active area of work grouping related knowledge.")):
    register_entity_type(_n, _d)
for _n, _d in (("relates_to", "Generic association between two entities."),
               ("depends_on", "Source needs the destination."),
               ("originated_from", "Source was derived from the destination.")):
    register_relationship_type(_n, _d)
