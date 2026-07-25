"""The memory pass — turn journal notes into World Model knowledge (through the review gate).

This is the feature that makes the vault *grow Orion's mind*: each dated daily note is dense
with durable signal — who the user sees, what projects move, health, mood, recurring themes —
and a short note is exactly what a 3B model handles well. Two safeguards keep local-model
quality safe:

* **Retrieval grounding** — before extracting, we recall what the World Model already knows
  about this note's terms and tell the model to surface only what's NEW, cutting duplicate
  candidates and anchoring the small model.
* **The review-inbox gate** — every candidate goes through ``world_model.ingest_candidate``,
  so nothing commits silently; the user accepts/edits/rejects in Orion's existing /reviews UI.

The daily-note date (from the filename) is threaded into each candidate so memories are
temporally anchored. Nothing here writes to the vault — it only reads notes and proposes
knowledge.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from orion.core.world_model import world_model

from . import llm, notes

log = logging.getLogger("orion.curator.memory")

_DATE = re.compile(r"(\d{4}-\d{1,2}-\d{1,2})")
_ENTITY_TYPES = {"person", "place", "project", "org", "concept", "preference"}
_KINDS = {"fact", "observation", "idea"}

_SYSTEM = (
    "You build a person's long-term memory from their private journal. Extract ONLY durable "
    "knowledge — things still worth knowing about them months or years from now.\n"
    "KEEP: who people are and their relationship to the author; projects and a real change in "
    "their status; lasting goals; health conditions; stable preferences and traits; genuinely "
    "significant life events (moves, jobs, milestones).\n"
    "DROP (return nothing for these): moods and feelings for the day (\"felt sleepy\", \"a "
    "difficult day\"), routine daily activities (\"studied at noon\", \"finished today's goals\"), "
    "one-off exam scores or task logs, and anything only true for that single day. When unsure, "
    "leave it out — a small memory of real facts beats a large one full of diary noise.\n"
    "Return JSON: {\"memories\": [{\"entity\": str, \"entity_type\": "
    "\"person|place|project|org|concept|preference\", \"key\": short label, "
    "\"value\": the durable fact in a full clear sentence, "
    "\"quote\": the exact sentence(s) from the entry this is based on, "
    "\"kind\": \"fact|observation|idea\", \"confidence\": 0.0-1.0}]}. "
    "'entity' is who/what the memory is about (use \"user\" for the author). Prefer confidence "
    "0.5-0.85. Return an empty list if the entry is just an everyday log. JSON only."
)

# A last-line guard against the local model's habit of logging daily trivia as "durable".
# These are cheap, high-precision drops; the review inbox remains the real backstop.
_TRIVIA_KEY = re.compile(
    r"\b(sleep|sleepy|mood|tired|nap|woke|breakfast|lunch|dinner|"
    r"today|tonight|morning|evening|afternoon|difficulty|difficult|routine)\b", re.I)
_EPHEMERAL_VALUE = re.compile(
    r"^\W*(felt|feeling|was (very|quite|so|a bit)|had a (good|bad|decent|rough|long|"
    r"productive|difficult)|started studying|studied|completed today|finished today|"
    r"woke up|went to (bed|sleep)|difficult|tough|boring|productive|unproductive|"
    r"tiring)\b", re.I)


def _worth_keeping(value: str, key: str, confidence: float) -> bool:
    """Reject obvious single-day trivia the prompt should have skipped. Conservative: only
    drops clear log-noise, so real facts phrased plainly still pass."""
    stripped = re.sub(r"^\[\d{4}-\d{1,2}-\d{1,2}\]\s*", "", value).strip()
    if len(stripped.split()) < 3 and not any(c.isupper() for c in stripped[1:]):
        return False                       # ultra-short with no proper noun ("very sleepy")
    if _EPHEMERAL_VALUE.match(stripped):
        return False
    # keys are snake_case; normalize so word boundaries fire ("paper_difficulty" -> "difficulty")
    if _TRIVIA_KEY.search(key.replace("_", " ")) and confidence < 0.8:
        return False
    return True


def _date_of(rel: str) -> str | None:
    m = _DATE.search(rel)
    return m.group(1) if m else None


def _grounding(text: str) -> str:
    """A compact digest of what the World Model already knows about this note's terms."""
    try:
        hits = world_model.recall(text[:400], limit=6)
    except Exception:
        return ""
    if not hits:
        return ""
    lines = [f"- {h.get('name')}: {h.get('value')}" for h in hits[:6]]
    return ("Already known (extract only NEW knowledge, do not repeat these):\n"
            + "\n".join(lines) + "\n\n")


def _valid(m: dict[str, Any]) -> bool:
    return (isinstance(m, dict) and m.get("entity") and m.get("value")
            and m.get("entity_type", "concept") in _ENTITY_TYPES | {""}
            and m.get("kind", "observation") in _KINDS | {""})


async def mine_note(text: str, rel: str) -> dict[str, int]:
    """Extract knowledge candidates from one note and route them through the review gate."""
    date = _date_of(rel)
    prompt = _grounding(text) + (f"Journal entry for {date}:\n" if date else "") + text
    data = await llm.extract(_SYSTEM, prompt)
    items = llm.as_list(data, "memories")

    tally = {"queued": 0, "accepted": 0, "discarded": 0}
    for m in items:
        if not _valid(m):
            continue
        value = m["value"].strip()
        key = (m.get("key") or "note").strip()
        confidence = float(m.get("confidence", 0.5))
        if not _worth_keeping(value, key, confidence):
            tally["discarded"] += 1
            continue
        if date and date not in value:
            value = f"[{date}] {value}"
        candidate = {
            "entity": str(m["entity"]).strip(),
            "entity_type": m.get("entity_type") or "concept",
            "key": key,
            "value": value,
            "quote": (m.get("quote") or "").strip(),   # source sentence, shown in the inbox
            "kind": m.get("kind") or "observation",
            "confidence": confidence,
            "source": f"vault:{rel}",
        }
        try:
            outcome = world_model.ingest_candidate(candidate)
        except Exception as e:
            log.info("ingest failed for %s: %s", rel, e)
            continue
        tally[outcome["outcome"]] = tally.get(outcome["outcome"], 0) + 1
    return tally


def mineable(text: str) -> bool:
    return notes.editable(notes.classify(text))
