"""Treasurer job orchestration: collect, model, interpret, persist."""
from __future__ import annotations

import json
import logging
from typing import Any

from orion.core.config import config

from . import analytics, inference, models, source, store

log = logging.getLogger("orion.treasurer")


def settings() -> dict[str, Any]:
    return config.section("treasurer")


async def collect() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cfg = settings()
    if not cfg.get("enabled", True):
        raise RuntimeError("Treasurer is disabled in config/treasurer.json")
    raw = await source.transactions()
    payload, rows = analytics.snapshot(raw, cfg)
    c = store.conn()
    try:
        payload["snapshot_id"] = store.add_snapshot(c, payload)
        store.set_meta(c, "last_source_error", "")
    finally:
        c.close()
    return payload, rows


async def refresh() -> dict[str, Any]:
    try:
        payload, _ = await collect()
        return {"ok": True, "transactions": payload["transaction_count"],
                "latest": payload.get("latest_transaction_at"), "snapshot": payload.get("snapshot_id")}
    except Exception as exc:
        c = store.conn()
        try:
            store.set_meta(c, "last_source_error", f"{type(exc).__name__}: {str(exc)[:300]}")
            cached = store.latest_snapshot(c)
        finally:
            c.close()
        log.warning("Treasurer refresh failed: %s", exc)
        return {"ok": False, "error": str(exc), "cached": bool(cached),
                "cached_at": cached.get("cached_at") if cached else None}


async def analyze(*, use_llm: bool = True) -> dict[str, Any]:
    try:
        payload, rows = await collect()
    except Exception as exc:
        c = store.conn()
        try:
            store.set_meta(c, "last_source_error", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            c.close()
        # Crucially, do not resolve old alerts when the source disappeared; absence of evidence
        # is not evidence that the user's spending returned to normal.
        return {"ok": False, "error": str(exc), "preserved_existing_insights": True}

    cfg = settings()
    findings, forecast = analytics.detect(payload, rows, cfg)
    if use_llm:
        findings = await inference.interpret(findings, cfg)
    method = forecast["method"] if forecast else "robust_median_mad"
    metrics = models.model_metrics(forecast)
    c = store.conn()
    try:
        model_run = store.add_model_run(
            c, version=(forecast or {}).get("version", models.MODEL_VERSION),
            trained_through=(forecast or {}).get("trained_through", payload.get("latest_transaction_at")),
            method=method, samples=int((forecast or {}).get("samples", len(rows))), metrics=metrics)
        ids = store.upsert_insights(c, findings, model_run)
        store.set_meta(c, "last_analysis", store.now())
    finally:
        c.close()
    return {"ok": True, "transactions": len(rows), "insights": len(ids),
            "model": method, "ml": bool(forecast), "llm": any(i.get("llm") for i in findings)}


async def train() -> dict[str, Any]:
    """Validate the current model without spending an LLM turn."""
    return await analyze(use_llm=False)


def remember_insight(item: dict[str, Any]) -> dict[str, Any]:
    """Commit an observation only after the user explicitly approves the Treasurer card."""
    from orion.core.world_model import world_model
    evidence = item.get("evidence", {})
    value = item["title"] + ". " + item["detail"]
    candidate = {
        "entity": "Personal finances", "entity_type": "financial_pattern",
        "key": item["kind"], "value": value[:1200], "kind": "observation",
        "confidence": 1.0, "source": f"treasurer:insight:{item['id']}",
        "quote": json.dumps(evidence, default=str)[:600],
    }
    return world_model.ingest_candidate(candidate)
