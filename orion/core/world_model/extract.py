"""Knowledge extraction — turn conversation text into reviewable candidates.

Uses the local reflex model (free) to pull durable facts/observations about the user out of a
turn. Output is validated and each candidate goes through WorldModel.ingest_candidate, which
applies the confidence policy (auto-accept / review inbox / discard). Degrades to a no-op when
no local provider is available.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from orion.core.providers import router
from orion.core.providers.base import Message

log = logging.getLogger("orion.extract")

_PROMPT = (
    "From the exchange below, extract only DURABLE knowledge about the user or their world "
    "(preferences, facts, projects, goals, relationships) — not chit-chat. Return a JSON array; "
    "each item: {\"entity\": str, \"entity_type\": \"person|project|concept|preference\", "
    "\"key\": str, \"value\": str, \"kind\": \"fact|observation|idea\", "
    "\"confidence\": 0.0-1.0}. Return [] if nothing durable. JSON only.\n\n"
)


def _parse(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start:end + 1])
        candidates = [
            d for d in data if isinstance(d, dict) and d.get("entity") and d.get("value")
        ]
        # These are model inferences even when the model calls itself 100% confident. Keep them
        # below the auto-accept boundary so every chat-derived item passes through review.
        for candidate in candidates:
            try:
                candidate["confidence"] = min(float(candidate.get("confidence", 0.5)), 0.99)
            except (TypeError, ValueError):
                candidate["confidence"] = 0.5
        return candidates
    except json.JSONDecodeError:
        return []


async def extract_candidates(text: str) -> list[dict[str, Any]]:
    provider = router.get("ollama")
    if provider is None or not await provider.is_available():
        return []
    try:
        raw = await provider.complete([Message("user", _PROMPT + text)])
        return _parse(raw)
    except Exception as e:
        log.info("extraction skipped: %s", e)
        return []
