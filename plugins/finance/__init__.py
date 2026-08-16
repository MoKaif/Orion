"""Treasurer — Orion's evidence-backed personal-finance agent.

Treasurer reads FinStrive, learns personal baselines locally, asks an LLM to interpret only
validated evidence, and contributes concise findings to Herald. It is entirely read-only with
respect to FinStrive. Inferred durable knowledge enters Orion only after an inbox action.
"""
from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orion.core import plugin_sdk as orion

from .specialist import FinanceSpecialist
from .tools import FinanceInsightsTool, FinanceSummaryTool, SpendingBreakdownTool

router = APIRouter()


def register() -> None:
    from . import engine, report

    orion.add_agent(
        "treasurer", "Treasurer", tagline="Personal finance",
        blurb="Reads FinStrive without changing it, learns what normal spending looks like for "
              "you, and explains material changes with evidence and confidence. Its models run "
              "locally; hypotheses become lasting knowledge only when you approve them.",
        icon="wallet-cards", accent="observation", plugin="finance", order=35,
        summary=_summary, detail=_detail)
    orion.add_specialist(FinanceSpecialist())
    orion.add_tool(FinanceSummaryTool())
    orion.add_tool(SpendingBreakdownTool())
    orion.add_tool(FinanceInsightsTool())
    orion.add_entity_type("financial_pattern", "A user-approved recurring financial observation.",
                          plugin="finance")
    orion.add_entity_type("financial_goal", "A user-approved budget or financial target.",
                          plugin="finance")
    orion.add_relationship_type("affects_spending", "Context associated with a spending pattern.",
                                plugin="finance")

    orion.add_job("treasurer_refresh", "0 */4 * * *", engine.refresh, agent="treasurer",
                  label="Refresh FinStrive",
                  description="Reads new transactions and refreshes Treasurer's local aggregate cache.")
    orion.add_job("treasurer_analyze", "0 7 * * *", engine.analyze, agent="treasurer",
                  label="Find financial patterns",
                  description="Runs personal baselines and ML anomaly models, then asks the LLM "
                              "to explain only the evidence they establish.")
    orion.add_job("treasurer_train", "30 6 * * 0", engine.train, agent="treasurer",
                  label="Validate the models",
                  description="Retrains and validates expected-spending models without using an LLM turn.")
    orion.add_widget("treasurer_spending", "Treasurer", _render_widget, plugin="finance")
    orion.add_inbox_source("treasurer", _inbox_items, plugin="finance")
    orion.add_report_source("treasurer", sections=report.sections, facts=report.facts,
                            alerts=report.alerts, plugin="finance")


def _state() -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None, str]:
    from . import store
    c = store.conn()
    try:
        return (store.latest_snapshot(c), store.insights(c, limit=20),
                store.latest_model_run(c), store.get_meta(c, "last_source_error") or "")
    finally:
        c.close()


def status() -> dict[str, Any]:
    snap, items, model, error = _state()
    return {
        "ok": snap is not None and not error,
        "state": "ready" if snap and not error else "cached" if snap else "waiting",
        "reason": error or ("Ready." if snap else "Run an analysis after FinStrive is available."),
        "source": "FinStrive read-only transaction API",
        "cached_at": snap.get("cached_at") if snap else None,
        "latest_transaction_at": snap.get("latest_transaction_at") if snap else None,
        "model": model,
        "active_insights": len(items),
    }


def _summary() -> dict[str, Any]:
    snap, items, model, error = _state()
    figures = (snap or {}).get("periods", {}).get("month_to_date", {})
    return {
        "pending": sum(i["review_state"] == "pending" for i in items),
        "metrics": [
            {"label": "spent MTD", "value": f"₹{figures.get('spending', 0):,.0f}" if snap else "—"},
            {"label": "active insights", "value": len(items)},
            {"label": "model", "value": "ML" if model and model["metrics"].get("available") else "robust"},
            {"label": "source", "value": "cached" if error and snap else "ready" if snap else "waiting"},
        ],
    }


def _detail() -> dict[str, Any]:
    snap, items, model, _ = _state()
    return {"treasurer": status(), "finance_snapshot": snap,
            "finance_insights": items, "finance_model": model}


def _render_widget() -> str:
    snap, items, _, error = _state()
    if snap is None:
        return '<p class="empty">Treasurer is waiting for its first FinStrive analysis.</p>'
    week = snap["periods"]["last_7d"]
    warning = f'<p class="muted">Cached — {escape(error)}</p>' if error else ""
    insight = (f'<p><strong>{escape(items[0]["title"])}</strong><br>'
               f'{escape(items[0]["detail"])}</p>') if items else '<p class="muted">No material change detected.</p>'
    return (f'<div class="stat"><b>₹{week["spending"]:,.0f}</b><span>spent in 7 days</span></div>'
            f'{warning}{insight}<a class="btn link-more" href="/agents/treasurer">the evidence →</a>')


def _inbox_items() -> list[dict[str, Any]]:
    from . import store
    c = store.conn()
    try:
        pending = store.insights(c, review_state="pending", limit=6)
    finally:
        c.close()
    out = []
    for item in pending:
        evidence = item["evidence"]
        effect = ("Remember stores this as an accepted observation in Orion's World Model. "
                  "Expected or dismiss only teaches Treasurer how to treat this finding; neither "
                  "changes FinStrive or moves money.")
        out.append({
            "origin": "treasurer", "id": item["id"], "title": item["title"],
            "body": item["detail"], "effect": effect, "created_at": item["first_seen_at"],
            "prov_agent": "Treasurer", "prov_label": f"{item['method']} · {item['confidence']:.0%}",
            "action_url": f"/plugins/finance/insights/{item['id']}",
            "payload": {"kind": item["kind"], "value": item["detail"], "evidence": evidence},
            "actions": [
                orion.inbox_action("Remember this pattern", "remember", "accept"),
                orion.inbox_action("This was expected", "expected", "neutral"),
                orion.inbox_action("Dismiss", "dismissed", "reject"),
            ],
        })
    return out


class InsightAction(BaseModel):
    action: str


@router.get("/status")
async def api_status():
    return status()


@router.get("/snapshot")
async def api_snapshot():
    snap, _, _, _ = _state()
    if snap is None:
        raise HTTPException(404, "Treasurer has no snapshot yet")
    return snap


@router.get("/insights")
async def api_insights(status: str = "active", limit: int = 50):
    from . import store
    c = store.conn()
    try:
        return store.insights(c, status=status, limit=max(1, min(200, limit)))
    finally:
        c.close()


@router.post("/refresh")
async def api_refresh():
    from . import engine
    return await engine.analyze()


@router.post("/insights/{insight_id}")
async def api_resolve_insight(insight_id: int, body: InsightAction):
    from . import engine, store
    action = body.action
    if action not in {"remember", "useful", "expected", "dismissed"}:
        raise HTTPException(400, "action must be remember, useful, expected, or dismissed")
    c = store.conn()
    try:
        item = store.insight(c, insight_id)
        if item is None:
            raise HTTPException(404, "insight not found")
        outcome = engine.remember_insight(item) if action == "remember" else None
        state = "remembered" if action == "remember" else action
        store.review(c, insight_id, state)
    finally:
        c.close()
    return {"ok": True, "state": state, "world_model": outcome}
