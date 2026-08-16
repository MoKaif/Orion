"""LLM interpretation of already-computed Treasurer evidence.

The model is not asked to add, total, classify, or detect anomalies. It receives findings whose
numbers have already been validated and may only explain drivers, connect relevant World Model
context, form labelled hypotheses, and suggest a reversible next step.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from orion.core.cognition import Mode
from orion.core.providers import router
from orion.core.providers.base import Message

log = logging.getLogger("orion.treasurer.inference")

_SYSTEM = """You are Treasurer, Orion's personal-finance inference agent.
You receive computed findings. Their numbers are authoritative: never recalculate, alter, or
invent them. For each finding return one concise explanation, zero or one explicitly tentative
hypothesis, and zero or one practical reversible suggestion. Use World Model context only when
it directly supports a connection. Never diagnose, shame, moralize, or give investment advice.
Return JSON only: {"insights":[{"fingerprint":str,"explanation":str,
"hypothesis":str|null,"suggestion":str|null,"context_used":[str]}]}.
"""


def _context(findings: list[dict[str, Any]]) -> list[str]:
    try:
        from orion.core.world_model import world_model
        query = "personal spending context " + " ".join(str(i.get("scope", "")) for i in findings)
        hits = world_model.recall(query, limit=6)
        return [f"{h.get('name')}: {h.get('value')}" for h in hits if h.get("value")][:6]
    except Exception:
        return []


def _safe_findings(findings: list[dict[str, Any]], share_merchants: bool) -> list[dict[str, Any]]:
    out = []
    for item in findings:
        evidence = dict(item.get("evidence", {}))
        if not share_merchants:
            evidence.pop("merchant", None)
        out.append({"fingerprint": item["fingerprint"], "kind": item["kind"],
                    "scope": item.get("scope"), "title": item["title"],
                    "evidence": evidence, "confidence": item.get("confidence")})
    return out


def _parse(raw: str) -> dict[str, dict[str, Any]]:
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    out = {}
    for item in data.get("insights", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not item.get("fingerprint") or not item.get("explanation"):
            continue
        out[str(item["fingerprint"])] = {
            "explanation": str(item["explanation"])[:500],
            "hypothesis": str(item["hypothesis"])[:400] if item.get("hypothesis") else None,
            "suggestion": str(item["suggestion"])[:300] if item.get("suggestion") else None,
            "context_used": [str(x)[:200] for x in (item.get("context_used") or [])[:4]],
        }
    return out


async def interpret(findings: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    lcfg = cfg.get("llm", {})
    if not lcfg.get("enabled", True) or not findings:
        return findings
    selected = findings[:int(lcfg.get("max_insights_per_run", 6))]
    block = {"findings": _safe_findings(selected, bool(lcfg.get("share_merchant_names", False))),
             "relevant_world_context": _context(selected)}
    try:
        chunks = []
        async for token in router.route(
                Mode.REASONING,
                [Message("system", _SYSTEM, cacheable=True),
                 Message("user", json.dumps(block, indent=2, default=str))]):
            chunks.append(token)
        interpreted = _parse("".join(chunks))
    except Exception as exc:
        log.info("Treasurer LLM interpretation unavailable: %s", exc)
        return findings
    for item in findings:
        if item["fingerprint"] in interpreted:
            item["llm"] = interpreted[item["fingerprint"]]
            item["detail"] = interpreted[item["fingerprint"]]["explanation"]
    return findings
