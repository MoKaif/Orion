"""Vitalist job orchestration."""
from __future__ import annotations

import logging
from typing import Any

from orion.core.config import config

from . import analysis, inference, source, store

log = logging.getLogger("orion.vitalist")


def settings() -> dict[str, Any]:
    return config.section("vitalist")


async def analyze(scope: str) -> dict[str, Any]:
    cfg = settings()
    if not cfg.get("enabled", True):
        return {"ok": False, "reason": "Vitalist is disabled"}
    try:
        raw = await source.collect()
        facts = analysis.build(raw, scope)
        prose = await inference.interpret(facts, cfg)
        c = store.conn()
        try:
            report_id = store.save(c, facts, prose)
            store.set_meta(c, "last_source_error", "")
        finally:
            c.close()
        return {"ok": True, "scope": scope, "report_id": report_id,
                "metrics": len(facts["available_metrics"]),
                "insights": len(prose["insights"]), "prose": prose["generated_by"]}
    except Exception as exc:
        c = store.conn()
        try:
            store.set_meta(c, "last_source_error", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            c.close()
        log.warning("Vitalist %s analysis failed: %s", scope, exc)
        return {"ok": False, "scope": scope, "error": str(exc),
                "preserved_cached_report": True}


async def daily() -> dict[str, Any]:
    return await analyze("daily")


async def weekly() -> dict[str, Any]:
    return await analyze("weekly")
