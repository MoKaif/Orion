"""The backlink pass — weave ``[[wikilinks]]`` so Obsidian's graph view comes alive.

315 daily notes with no links render as disconnected dust; the graph only has edges where a
note links out. Obsidian computes the *back*links automatically, so this pass only has to
insert forward ``[[Entity]]`` links into the dailies — and it links **only registry entities**
(closed-set matching), which keeps a small model from over-linking stray words. It links the
first unlinked occurrence of each entity per note and never writes inside YAML frontmatter,
code fences/spans, existing links, or headings.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import entities, grammar, notes

# spans that must never be edited: frontmatter, fenced code, inline code, existing links
_PROTECTED = re.compile(
    r"(^---\n.*?\n---\n)"           # YAML frontmatter (leading)
    r"|(```.*?```)"                 # fenced code / dataview blocks
    r"|(`[^`]*`)"                   # inline code
    r"|(\[\[[^\]]*\]\])"            # existing wikilinks
    r"|(\[[^\]]*\]\([^)]*\))"       # markdown links
    r"|(^#{1,6} .*$)",              # headings
    re.DOTALL | re.MULTILINE)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]


def _in_protected(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def _link_form(name: str, surface: str) -> str:
    # exact match → bare link; any difference (incl. case) keeps the author's surface text
    return f"[[{name}]]" if surface == name else f"[[{name}|{surface}]]"


def _surface_forms(entity: dict[str, Any]) -> list[str]:
    forms = {entity["name"], *json.loads(entity["aliases"])}
    # longest first so "Chintu Bhaiya" wins over "Chintu"
    return sorted((f for f in forms if len(f) >= 3), key=len, reverse=True)


def weave(text: str, registry: list[dict[str, Any]]) -> str:
    """Return the note with one ``[[link]]`` inserted per entity's first clean mention."""
    linked_names: set[str] = set()
    for entity in registry:
        name = entity["name"]
        if name in linked_names:
            continue
        for surface in _surface_forms(entity):
            spans = _protected_spans(text)
            for m in re.finditer(rf"(?<!\w){re.escape(surface)}(?!\w)", text):
                if _in_protected(m.start(), spans):
                    continue
                repl = _link_form(name, m.group())
                text = text[:m.start()] + repl + text[m.end():]
                linked_names.add(name)
                break
            if name in linked_names:
                break
    return text


def propose_for(text: str, rel: str, registry: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(linked_text, diff) for a note, or None if nothing to link / class unsafe."""
    if not notes.editable(notes.classify(text)):
        return None
    woven = weave(text, registry)
    if woven == text:
        return None
    return woven, grammar.diff(text, woven, rel)


def linkable_registry(c) -> list[dict[str, Any]]:
    """Entities safe to link: approved, or already given a hub note."""
    return [dict(r) for r in c.execute(
        "SELECT * FROM entities WHERE status='approved' OR note_path IS NOT NULL "
        "ORDER BY mentions DESC")]
