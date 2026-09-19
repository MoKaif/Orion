"""Constrained prose over health facts that Vitalist did not compute itself."""
from __future__ import annotations

import json
import logging
from typing import Any

from orion.core.cognition import Mode
from orion.core.providers import router
from orion.core.providers.base import Message

log = logging.getLogger("orion.vitalist.inference")

_SYSTEM = """You are Vitalist, Orion's health-pattern interpreter.
The JSON contains health facts already computed by Perseus and Vitalist. Treat every number as
authoritative. Do not invent, recalculate, diagnose, predict disease, or prescribe treatment.
Missing data is missing, not evidence of good or poor health. Compare only the named periods.
Write a calm, direct summary and at most four useful observations. Prefer patterns and data
quality over generic advice. If mentioning a possible explanation, label it explicitly as a
hypothesis. Return JSON only: {"summary":str,"insights":[str]}.
"""


def _parse(raw: str) -> dict[str, Any] | None:
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        return None
    insights = [str(x)[:500] for x in (data.get("insights") or []) if isinstance(x, str)]
    return {"summary": data["summary"][:800], "insights": insights[:4], "generated_by": "llm"}


async def interpret(facts: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    fallback = {"summary": facts["findings"][0], "insights": facts["findings"],
                "generated_by": "deterministic"}
    if not cfg.get("llm", {}).get("enabled", True):
        return fallback
    try:
        chunks = []
        async for token in router.route(
                Mode.REASONING if facts["scope"] == "weekly" else Mode.REFLEX,
                [Message("system", _SYSTEM, cacheable=True),
                 Message("user", json.dumps(facts, indent=2, default=str))]):
            chunks.append(token)
        return _parse("".join(chunks)) or fallback
    except Exception as exc:
        log.info("Vitalist prose unavailable; using computed findings: %s", exc)
        return fallback
