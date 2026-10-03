from __future__ import annotations

from datetime import date, timedelta

from . import store


def sections(scope: str) -> list[dict]:
    c = store.conn()
    try:
        events = store.upcoming(c, 30)
        pending = store.proposals(c, limit=20)
    finally:
        c.close()
    days = 7 if scope in {"briefing", "daily"} else 31
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    selected = [event for event in events if event["start_at"][:10] <= cutoff]
    if not selected and not pending:
        return []
    rows = [(event["start_at"].replace("T", " ")[:16], event["summary"])
            for event in selected[:8]]
    note = f"{len(pending)} event proposal(s) await approval." if pending else ""
    return [{"heading": "Chronos", "blurb": f"{len(selected)} confirmed event(s) in the next {days} days.",
             "rows": rows, "bullets": [], "accent": "#60a5fa", "note": note}]


def facts(scope: str) -> dict:
    c = store.conn()
    try:
        events = store.upcoming(c, 30)
        pending = store.proposals(c, limit=20)
    finally:
        c.close()
    return {"chronos": {"confirmed_upcoming": events, "pending_proposals": len(pending)}}
