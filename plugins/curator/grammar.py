"""The grammar/spelling pass — Curator's original job, now classifier-aware.

Unchanged in spirit from v1: one local-model rewrite per new-or-changed note, queued as a
``kind='grammar'`` proposal that never touches disk until the user applies it. What v2 adds:

* it consults ``notes.classify`` first, so templates, quote collections, code-heavy notes and
  pasted AI output are skipped instead of mangled;
* a deterministic ``mechanical_fixes`` tier (collapse double spaces / trailing whitespace /
  runs of blank lines) that needs no model — offered for optional auto-apply via config, off by
  default so the human-review guarantee holds unless the user opts in.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

from . import llm, notes

_MAX_DIFF_LINES = 80

_FRONTMATTER = re.compile(r"\A(?:\ufeff)?---\r?\n.*?\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_FENCED_CODE = re.compile(r"^```.*?^```[^\n]*(?:\n|\Z)", re.DOTALL | re.MULTILINE)
_WIKILINK = re.compile(r"!?\[\[[^\]]+\]\]")
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]+\]\([^)]+\)")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://[^\s)>]+")
_TAG = re.compile(r"(?<!\w)#[\w/-]+")
_HEADING = re.compile(r"^(#{1,6}\s+.*)$", re.MULTILINE)
_LIST_PREFIX = re.compile(r"^(\s*(?:(?:[-+*])|(?:\d+[.)]))\s+)")

_SYSTEM = (
    "You are Curator, the careful editor of a personal Obsidian vault. Correct ONLY "
    "grammar, spelling, and punctuation in the note you are given. Preserve the author's "
    "meaning, tone, and voice exactly. Never touch: YAML frontmatter, wiki-links like "
    "[[Note Name]], markdown links, tags (#tag), code blocks, URLs, headings' wording, or "
    "list structure. Keep every line break exactly where the author put it — never merge or "
    "split lines. Return JSON: {\"corrected\": \"<full corrected note text>\"}. If the note "
    "needs no correction, return the text unchanged."
)


def diff(a: str, b: str, path: str) -> str:
    lines = list(difflib.unified_diff(
        a.splitlines(), b.splitlines(), fromfile=path, tofile=path, lineterm="", n=1))
    if len(lines) > _MAX_DIFF_LINES:
        lines = lines[:_MAX_DIFF_LINES] + [f"… ({len(lines) - _MAX_DIFF_LINES} more lines)"]
    return "\n".join(lines)


def mechanical_fixes(text: str) -> str:
    """Deterministic, meaning-preserving cleanups — no model involved."""
    out = []
    for line in text.split("\n"):
        # collapse internal double+ spaces (but leave leading indentation intact)
        indent = len(line) - len(line.lstrip(" "))
        body = re.sub(r"  +", " ", line[indent:]).rstrip()
        out.append(line[:indent] + body)
    joined = "\n".join(out)
    joined = re.sub(r"\n{3,}", "\n\n", joined)   # cap blank-line runs
    return joined


def preserves_structure(original: str, corrected: str) -> bool:
    """Whether a model rewrite kept every non-prose part of a note byte-for-byte.

    Prompt instructions are not a safety boundary.  This check is: a grammar proposal is
    discarded if it changes frontmatter, code, links, tags, URLs, headings, list markers, or
    line layout.  Comparing counters also catches a protected token being moved between lines.
    """
    if original.count("\n") != corrected.count("\n"):
        return False

    for pattern in (_FRONTMATTER, _FENCED_CODE):
        if Counter(pattern.findall(original)) != Counter(pattern.findall(corrected)):
            return False

    for before, after in zip(original.splitlines(), corrected.splitlines()):
        for pattern in (_WIKILINK, _MARKDOWN_LINK, _INLINE_CODE, _URL, _TAG, _HEADING):
            if Counter(pattern.findall(before)) != Counter(pattern.findall(after)):
                return False
        before_prefix = _LIST_PREFIX.match(before)
        after_prefix = _LIST_PREFIX.match(after)
        if ((before_prefix.group(1) if before_prefix else None)
                != (after_prefix.group(1) if after_prefix else None)):
            return False
    return True


async def correct(text: str) -> str | None:
    """One JSON-constrained local grammar pass. None means 'no usable correction'."""
    data = await llm.extract(_SYSTEM, text)
    if not isinstance(data, dict):
        return None
    out = (data.get("corrected") or "").strip("\n")
    if not out:
        return None
    # sanity: a grammar pass should not change the note's size much
    if abs(len(out) - len(text)) > max(200, len(text) * 0.2):
        return None
    # preserve the note's exact trailing-newline shape
    out += re.search(r"\n*$", text).group()
    return out if preserves_structure(text, out) else None


async def propose_for(text: str, rel: str) -> tuple[str, str] | None:
    """Return (corrected_text, diff) for a note, or None if nothing worth proposing.

    Skips classes the editor must not touch; only 'journal'/'prose' reach the model.
    """
    if not notes.editable(notes.classify(text)):
        return None
    corrected = await correct(text)
    if not corrected or corrected == text:
        return None
    return corrected, diff(text, corrected, rel)
