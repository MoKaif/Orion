"""Vitalist — Orion's evidence-backed personal-health agent."""
from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException

from orion.core import plugin_sdk as orion

from .specialist import HealthSpecialist
from .tools import HealthSummaryTool

router = APIRouter()


def register() -> None:
    from . import engine, report

    orion.add_agent(
        "vitalist", "Vitalist", tagline="Personal health",
        blurb="Reads activity, sleep, heart-rate, and workout facts from Perseus; compares them "
              "with your own baselines; and gives Herald a concise daily and weekly account. "
              "It identifies patterns, not diagnoses, and treats missing tracker data as unknown.",
        icon="compass", accent="fact", plugin="health", order=40,
        summary=_summary, detail=_detail)
    orion.add_specialist(HealthSpecialist())
    orion.add_tool(HealthSummaryTool())
    orion.add_job("vitalist_daily", "45 6 * * *", engine.daily, agent="vitalist",
                  label="Daily health insight",
                  description="Compares yesterday's Perseus activity, sleep, and heart-rate data "
                              "with personal baselines before Herald's morning briefing.")
    orion.add_job("vitalist_weekly", "15 8 * * 1", engine.weekly, agent="vitalist",
                  label="Weekly health insight",
                  description="Compares the last seven complete days with the preceding week "
                              "before Herald's Monday letter.")
    orion.add_widget("vitalist_health", "Vitalist", _render_widget, plugin="health")
    orion.add_report_source("vitalist", sections=report.sections, facts=report.facts,
                            plugin="health")


def _reports() -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    from . import store
    c = store.conn()
    try:
        return store.latest(c, "daily"), store.latest(c, "weekly"), \
               store.get_meta(c, "last_source_error") or ""
    finally:
        c.close()


def status() -> dict[str, Any]:
    from . import source
    daily, weekly, error = _reports()
    return {"ok": bool(daily) and not error, "state": "ready" if daily and not error else
            "cached" if daily else "waiting", "reason": error or
            ("Ready." if daily else "Run the daily pass after configuring Perseus."),
            "perseus": source.base_url() or "not configured",
            "latest_daily": daily.get("created_at") if daily else None,
            "latest_weekly": weekly.get("created_at") if weekly else None}


def _summary() -> dict[str, Any]:
    daily, weekly, error = _reports()
    metrics = (daily or {}).get("facts", {}).get("available_metrics", [])
    return {"pending": 0, "metrics": [
        {"label": "daily", "value": "ready" if daily else "waiting"},
        {"label": "weekly", "value": "ready" if weekly else "waiting"},
        {"label": "signals", "value": len(metrics)},
        {"label": "source", "value": "cached" if error and daily else "Perseus" if daily else "offline"},
    ]}


def _detail() -> dict[str, Any]:
    daily, weekly, _ = _reports()
    return {"vitalist": status(), "health_daily": daily, "health_weekly": weekly}


def _render_widget() -> str:
    daily, _, error = _reports()
    if daily is None:
        return '<p class="empty">Vitalist is waiting for its first Perseus analysis.</p>'
    prose = daily["prose"]
    warning = f'<p class="muted">Cached — {escape(error)}</p>' if error else ""
    return (f'<p><strong>{escape(prose.get("summary", "Health report ready."))}</strong></p>'
            f'{warning}<a class="btn link-more" href="/agents/vitalist">the evidence →</a>')


@router.get("/status")
async def api_status():
    return status()


@router.get("/reports/latest")
async def api_latest_report(scope: str = "daily"):
    from . import store
    if scope not in {"daily", "weekly"}:
        raise HTTPException(400, "scope must be daily or weekly")
    c = store.conn()
    try:
        report = store.latest(c, scope)
    finally:
        c.close()
    if report is None:
        raise HTTPException(404, f"Vitalist has no {scope} report yet")
    return report
