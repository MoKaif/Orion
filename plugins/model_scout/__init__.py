"""Model Scout — evidence-based, review-gated model evolution for Orion."""
from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orion.core import plugin_sdk as orion

router = APIRouter()


def register() -> None:
    from . import engine
    orion.add_agent(
        "model_scout", "Model Scout", tagline="Model fitness",
        blurb="Measures Orion's models on Orion's own work, watches credible lightweight "
              "candidates, and proposes upgrades with evidence. It never downloads or switches "
              "a model without review.",
        icon="bot", accent="idea", plugin="model_scout", order=38,
        summary=_summary, detail=_detail)
    orion.add_job("model_scout_research", "30 4 * * 0", engine.research,
                  agent="model_scout", label="Refresh model research",
                  description="Checks pinned official model records for revisions and records "
                              "candidate availability; no model is downloaded.")
    orion.add_job("model_scout_benchmark", "0 5 1 * *", engine.benchmark,
                  agent="model_scout", label="Benchmark installed models",
                  description="Runs a bounded Orion-specific decision benchmark against installed "
                              "Ollama models and an available Laya service.")
    orion.add_inbox_source("model_scout", _inbox_items, plugin="model_scout")
    orion.add_widget("model_scout_status", "Model Scout", _widget, plugin="model_scout")


def _summary() -> dict[str, Any]:
    from . import store
    c = store.conn()
    try:
        latest = store.latest_run(c)
        pending = len(store.recommendations(c))
    finally:
        c.close()
    return {"pending": pending, "metrics": [
        {"label": "current", "value": (latest or {}).get("current_model", "not tested")},
        {"label": "tested", "value": len((latest or {}).get("results", []))},
        {"label": "recommendations", "value": pending},
        {"label": "state", "value": "measured" if latest else "waiting"},
    ]}


def _detail() -> dict[str, Any]:
    from . import store
    c = store.conn()
    try:
        return {"model_scout": status(), "model_research": store.metadata(c, "latest_research"),
                "model_runs": store.runs(c, 12),
                "model_recommendations": store.recommendations(c, status=None, limit=30)}
    finally:
        c.close()


def status() -> dict[str, Any]:
    from orion.core.config import config
    from . import store
    c = store.conn()
    try:
        latest = store.latest_run(c)
        research = store.metadata(c, "latest_research")
    finally:
        c.close()
    current = config.provider_cfg("ollama").get("model", "unknown")
    return {"ok": latest is not None, "state": "measured" if latest else "waiting",
            "current_model": current, "last_benchmark": (latest or {}).get("created_at"),
            "last_research": (research or {}).get("updated_at"),
            "reason": "" if latest else "Run the benchmark after Ollama or Laya is available."}


def _inbox_items() -> list[dict[str, Any]]:
    from . import store
    c = store.conn()
    try:
        items = store.recommendations(c)
    finally:
        c.close()
    return [{
        "origin": "model_scout", "id": item["id"], "title": item["title"],
        "body": item["body"], "effect": item["effect"], "created_at": item["created_at"],
        "prov_agent": "Model Scout", "prov_label": item["kind"].replace("_", " ").title(),
        "action_url": f'/plugins/model_scout/recommendations/{item["id"]}',
        "payload": item["evidence"],
        "actions": [orion.inbox_action(
            "Use this model" if item["kind"] == "switch" else "Mark for trial", "accept", "accept"),
            orion.inbox_action("Keep current setup", "reject", "reject")],
    } for item in items]


def _widget() -> str:
    state = status()
    if not state["ok"]:
        return '<p class="empty">No model fitness run yet. Open Model Scout and run the benchmark.</p>'
    return (f'<p><strong>{escape(state["current_model"])}</strong> is the measured local model.</p>'
            f'<p class="muted">Last benchmark: {escape(state["last_benchmark"] or "—")}</p>'
            '<a class="btn link-more" href="/agents/model_scout">see the evidence →</a>')


class Action(BaseModel):
    action: str


@router.get("/status")
async def api_status():
    return status()


@router.get("/runs")
async def api_runs(limit: int = 20):
    from . import store
    c = store.conn()
    try:
        return store.runs(c, min(100, max(1, limit)))
    finally:
        c.close()


@router.post("/recommendations/{rid}")
async def api_resolve(rid: int, body: Action):
    from . import engine
    result = await engine.resolve(rid, body.action)
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason", "recommendation could not be resolved"))
    return result
