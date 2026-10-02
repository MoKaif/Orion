"""Chronos — Orion's calendar and commitment agent."""
from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orion.core import plugin_sdk as orion

from .specialist import CalendarSpecialist
from .tools import UpcomingScheduleTool

router = APIRouter()


def register() -> None:
    from . import engine, report
    orion.add_agent(
        "chronos", "Chronos", tagline="Time and commitments",
        blurb="Keeps Orion aligned with your primary Google Calendar, scans Gmail read-only for "
              "explicit upcoming commitments, and asks before adding anything inferred. Calendar "
              "writes happen only when you approve the corresponding Inbox card.",
        icon="calendar-days", accent="fact", plugin="chronos", order=32,
        summary=_summary, detail=_detail)
    orion.add_specialist(CalendarSpecialist())
    orion.add_tool(UpcomingScheduleTool())
    orion.add_entity_type("calendar_event", "A confirmed time-bound commitment.", plugin="chronos")
    orion.add_job("chronos_calendar_sync", "*/30 * * * *", engine.sync_calendar,
                  agent="chronos", label="Sync Google Calendar",
                  description="Refreshes the read-only local view of the next month.")
    orion.add_job("chronos_mail_scan", "20 0 * * *", engine.scan_mail,
                  agent="chronos", label="Find commitments in mail",
                  description="Runs once nightly with a one-thread local-model budget, reads "
                              "recent Gmail without changing flags, and proposes explicit "
                              "calendar commitments for approval.")
    orion.add_inbox_source("chronos", _inbox_items, plugin="chronos")
    orion.add_report_source("chronos", sections=report.sections, facts=report.facts,
                            plugin="chronos")
    orion.add_widget("chronos_upcoming", "Chronos", _render_widget, plugin="chronos")


def status() -> dict[str, Any]:
    from . import calendar, store
    authorized, reason = calendar.authorized()
    c = store.conn()
    try:
        events = store.upcoming(c, 30)
        pending = store.proposals(c, limit=50)
        calendar_error = store.get_meta(c, "last_calendar_error") or ""
        mail_error = store.get_meta(c, "last_mail_error") or ""
        last_sync = store.get_meta(c, "last_calendar_sync")
        last_mail = store.get_meta(c, "last_mail_scan")
    finally:
        c.close()
    healthy = authorized and not calendar_error and not mail_error
    return {"ok": healthy,
            "state": "ready" if healthy else "authorization_required" if not authorized else "degraded",
            "reason": calendar_error or mail_error or reason,
            "authorized": authorized, "confirmed_events": len(events),
            "pending_proposals": len(pending), "last_calendar_sync": last_sync,
            "last_mail_scan": last_mail}


def _summary() -> dict[str, Any]:
    state = status()
    return {"pending": state["pending_proposals"], "metrics": [
        {"label": "confirmed", "value": state["confirmed_events"]},
        {"label": "mail proposals", "value": state["pending_proposals"]},
        {"label": "calendar", "value": "connected" if state["authorized"] else "authorize"},
        {"label": "state", "value": state["state"]},
    ]}


def _detail() -> dict[str, Any]:
    from . import store
    c = store.conn()
    try:
        return {"chronos": status(), "calendar_events": store.upcoming(c, 30),
                "calendar_proposals": store.proposals(c, limit=30)}
    finally:
        c.close()


def _render_widget() -> str:
    from . import store
    c = store.conn()
    try:
        events = store.upcoming(c, 3)
        pending = len(store.proposals(c, limit=20))
    finally:
        c.close()
    if not events:
        return '<p class="empty">Chronos has no confirmed upcoming events cached yet.</p>'
    rows = "".join(f'<li><strong>{escape(event["summary"])}</strong><br>'
                   f'<span class="muted">{escape(event["start_at"][:16].replace("T", " "))}</span></li>'
                   for event in events)
    note = f'<p class="muted">{pending} proposal(s) await review.</p>' if pending else ""
    return f'<ul>{rows}</ul>{note}<a class="btn link-more" href="/agents/chronos">open Chronos →</a>'


def _inbox_items() -> list[dict[str, Any]]:
    from . import store
    c = store.conn()
    try:
        pending = store.proposals(c, limit=12)
    finally:
        c.close()
    return [{
        "origin": "chronos", "id": item["id"], "title": item["title"],
        "body": f'{item["start_at"]} · {item.get("description") or "Explicit commitment found in mail."}',
        "effect": "Accept creates this event on your primary Google Calendar. Reject records "
                  "the decision locally and changes neither Gmail nor Calendar.",
        "created_at": item["created_at"], "prov_agent": "Chronos",
        "prov_label": f'Mail · {item["confidence"]:.0%}',
        "action_url": f'/plugins/chronos/proposals/{item["id"]}',
        "payload": {"start_at": item["start_at"], "end_at": item.get("end_at"),
                    "location": item.get("location")},
        "actions": [orion.inbox_action("Add to Google Calendar", "accept", "accept"),
                    orion.inbox_action("Not an event", "reject", "reject")],
    } for item in pending]


class ProposalAction(BaseModel):
    action: str


@router.get("/status")
async def api_status():
    return status()


@router.get("/authorize")
async def api_authorize():
    from . import calendar
    ok, reason = calendar.authorized()
    return {"ok": ok, "reason": reason,
            "command": ".venv/bin/python scripts/authorize_chronos.py" if not ok else None}


@router.post("/sync")
async def api_sync():
    from . import engine
    return {"calendar": await engine.sync_calendar(), "mail": await engine.scan_mail()}


@router.get("/proposals")
async def api_proposals(status: str = "pending"):
    from . import store
    c = store.conn()
    try:
        return store.proposals(c, status=status, limit=100)
    finally:
        c.close()


@router.post("/proposals/{pid}")
async def api_resolve(pid: int, body: ProposalAction):
    from . import engine
    result = await engine.resolve_proposal(pid, body.action)
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason", "proposal could not be resolved"))
    return result
