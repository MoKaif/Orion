"""Vault access + note classification — the shared front door for every Curator pass.

Two jobs live here:

* **Walking the vault** (respecting Obsidian's hidden dirs and the configured extensions) and
  building the ``obsidian://`` deep links the review UI uses.
* **Classifying notes** so a pass never wastes the local model on text it should not touch.
  Doing this hand-curation the first time taught the rules encoded in ``classify`` — templates
  (``{{placeholders}}``), quote collections, code/dataview-heavy notes and pasted AI output are
  *skip*; short structured daily logs are *journal*; everything else is *prose*. A cheap rule
  beating the model to the punch is a free quality win (and it keeps the 3B model off the ~5
  book/template notes that would only produce noise).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from orion.core.config import config

_SKIP_DIRS = {".obsidian", ".trash", ".git"}
_MAX_NOTE_CHARS = 8000     # local context is small; longer notes are left to a future pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def vault() -> Path | None:
    raw = config.section("settings").get("vault", {}).get("path", "")
    path = Path(raw).expanduser() if raw else None
    return path if path and path.is_dir() else None


def _extensions() -> tuple[str, ...]:
    return tuple(config.section("settings").get("vault", {}).get("extensions", [".md"]))


# the richest, most memory-dense content is processed first so bounded backfill passes reach
# it early (and low-signal scratch folders last).
_PRIORITY = ("Journal",)


def _walk_rank(rel: Path) -> tuple[int, str]:
    top = rel.parts[0] if rel.parts else ""
    return (0 if top in _PRIORITY else 1, str(rel))


def walk(root: Path) -> Iterator[Path]:
    """Every note file under the vault, journal-first, skipping Obsidian's hidden/system dirs."""
    exts = _extensions()
    candidates = []
    for p in root.rglob("*"):
        if p.suffix not in exts or not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        candidates.append((p, rel))
    for p, _rel in sorted(candidates, key=lambda pr: _walk_rank(pr[1])):
        yield p


def obsidian_uri(rel: str) -> str:
    """Deep link that opens the note in the Obsidian app for hand-editing.

    Obsidian resolves vaults by directory name; the .md suffix is optional in the file
    param, so it is dropped (other extensions are kept as-is).
    """
    v = vault()
    if v is None:
        return ""
    return (f"obsidian://open?vault={quote(v.name, safe='')}"
            f"&file={quote(rel.removesuffix('.md'), safe='/')}")


# -- classification --------------------------------------------------------
_TEMPLATE = re.compile(r"\{\{.*?\}\}")
_AI_TELLS = ("Here's your", "Here is your", "I'll design", "Let me design",
             "## 🧠", "Proposed Architecture", "Final Architecture")


def classify(text: str) -> str:
    """Return a handling class: skip/template/quotes/hub/raw_memory/journal/prose/code.

    Raw memories are safe to mine for entities and reviewable World Model knowledge, but their
    wording is source material and must never enter the grammar or backlink editing passes.
    """
    stripped = text.strip()
    if not stripped:
        return "skip"
    if len(text) > _MAX_NOTE_CHARS:
        return "skip"
    frontmatter_match = re.match(
        r"\A(?:\ufeff)?---\r?\n(?P<body>.*?)^---\s*$", text, re.DOTALL | re.MULTILINE)
    frontmatter = frontmatter_match.group("body") if frontmatter_match else ""
    # Curator-authored entity hubs are structured indexes, not prose. In particular their
    # frontmatter is machine-readable state and must never be sent through the grammar model.
    if re.search(r"^curator_entity_id:\s*\d+\s*$", frontmatter, re.MULTILINE):
        return "hub"
    if (re.search(r"^type:\s*raw-memory\s*$", frontmatter, re.MULTILINE | re.IGNORECASE)
            and re.search(r"^preserve_voice:\s*true\s*$", frontmatter,
                          re.MULTILINE | re.IGNORECASE)):
        return "raw_memory"
    if _TEMPLATE.search(text):
        return "template"
    # a note that is mostly a fenced code / dataview block is structure, not prose
    fenced = sum(len(m) for m in re.findall(r"```.*?```", text, re.DOTALL))
    if fenced > len(text) * 0.5:
        return "code"
    # quote collections: many lines are attributions ("— Author") or block quotes
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines:
        quotey = sum(ln.lstrip().startswith((">", "—", "-", "\"", "“")) for ln in lines)
        if quotey >= max(3, len(lines) * 0.6):
            return "quotes"
    if any(tell in text for tell in _AI_TELLS):
        return "skip"
    # short, structured, dated logs read as journal entries
    if len(stripped) < 2000:
        return "journal"
    return "prose"


def editable(cls: str) -> bool:
    """Whether Curator may rewrite the prose or insert backlinks."""
    return cls in ("journal", "prose")


def mineable(cls: str) -> bool:
    """Whether read-only entity and memory extraction may inspect this note."""
    return cls in ("journal", "prose", "raw_memory")
