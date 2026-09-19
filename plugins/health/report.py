"""Vitalist's computed news for Herald, via Orion's report registry."""
from __future__ import annotations

from typing import Any

from . import store

_GOOD = "#85bb9c"
_ATTENTION = "#d6b360"


def _latest(scope: str) -> dict[str, Any] | None:
    wanted = "weekly" if scope == "weekly" else "daily"
    c = store.conn()
    try:
        return store.latest(c, wanted)
    finally:
        c.close()


def _format_metric(item: dict[str, Any]) -> str:
    value = item.get("current")
    if value is None:
        return "not recorded"
    name, unit = item.get("name"), item.get("unit") or ""
    if name == "step_count":
        return f"{value:,.0f} / day" if item.get("current_days", 0) > 1 else f"{value:,.0f}"
    if name == "distance_walking_running":
        return f"{value:.1f} km" + (" / day" if item.get("current_days", 0) > 1 else "")
    if name in {"active_energy", "heart_rate"}:
        return f"{value:.0f} {unit}" + (" avg" if name == "heart_rate" else "")
    return f"{value:.1f} {unit}".strip()


def sections(scope: str) -> list[dict[str, Any]]:
    report = _latest(scope)
    if report is None:
        return []
    facts, prose = report["facts"], report["prose"]
    by_name = {m["name"]: m for m in facts["metrics"]}
    rows = []
    for name in ("step_count", "distance_walking_running", "active_energy", "heart_rate"):
        item = by_name.get(name)
        if item and item.get("current") is not None:
            rows.append((item["label"], _format_metric(item)))
    sleep = facts.get("sleep") or {}
    if sleep.get("average_hours") is not None:
        rows.append(("Sleep", f"{sleep['average_hours']:.1f} hr"))
    return [{
        "heading": "Vitalist's week" if scope == "weekly" else "Vitalist",
        "blurb": prose.get("summary", ""), "rows": rows,
        "bullets": prose.get("insights") or [],
        "accent": _ATTENTION if any(abs(m.get("change_pct") or 0) >= .2 for m in facts["metrics"]) else _GOOD,
        "note": f"Perseus data through {facts['period_end']}; informational, not medical advice.",
    }]


def facts(scope: str) -> dict[str, Any]:
    report = _latest(scope)
    if report is None:
        return {"vitalist": {"available": False}}
    return {"vitalist": {"available": True, "period": report["scope"],
                           "facts": report["facts"], "insights": report["prose"]}}
