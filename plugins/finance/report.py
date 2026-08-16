"""Treasurer's computed news for Herald, coupled only through the report registry."""
from __future__ import annotations

from typing import Any

from orion.core.config import config

from . import store

_GOOD = "#85bb9c"
_ATTENTION = "#d6b360"


def _read() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    c = store.conn()
    try:
        return store.latest_snapshot(c), store.insights(c, limit=12)
    finally:
        c.close()


def facts(scope: str) -> dict[str, Any]:
    snap, items = _read()
    if snap is None:
        return {"treasurer": {"available": False}}
    period = snap["periods"]["month_to_date" if scope == "weekly" else "last_7d"]
    return {"treasurer": {
        "available": True, "as_of": snap["as_of"], "figures": period,
        "active_insights": len(items),
        "findings": [{"title": i["title"], "confidence": i["confidence"],
                      "severity": i["severity"]} for i in items[:6]],
        "data_quality": snap["quality"],
    }}


def sections(scope: str) -> list[dict[str, Any]]:
    snap, items = _read()
    if snap is None:
        return []
    key = "month_to_date" if scope == "weekly" else "last_7d"
    figures = snap["periods"][key]
    rows = [("spent", f"₹{figures['spending']:,.0f}"),
            ("income", f"₹{figures['income']:,.0f}"),
            ("invested", f"₹{figures['investing']:,.0f}")]
    bullets = []
    for item in items[:4 if scope == "weekly" else 2]:
        suffix = f" ({item['confidence']:.0%} confidence)" if item["confidence"] < 1 else ""
        bullets.append(f"{item['detail']}{suffix}")
    return [{
        "heading": "Treasurer's week" if scope == "weekly" else "Treasurer",
        "rows": rows, "bullets": bullets,
        "accent": _ATTENTION if any(i["severity"] in {"attention", "critical"} for i in items) else _GOOD,
        "note": (f"Based on {snap['transaction_count']} FinStrive transactions; "
                 f"{snap['quality']['unmapped_count']} unmapped."),
    }]


def alerts() -> list[dict[str, Any]]:
    cfg = config.section("treasurer").get("alerts", {})
    threshold = float(cfg.get("minimum_confidence", .72))
    severities = set(cfg.get("severities", ["attention", "critical"]))
    c = store.conn()
    try:
        active = store.insights(c, limit=30)
        resolved = store.insights(c, status="resolved", limit=30)
    finally:
        c.close()
    out = []
    for item in active:
        key = f"treasurer:{item['fingerprint']}"
        if (item["severity"] in severities and item["confidence"] >= threshold
                and item["review_state"] not in {"expected", "dismissed"}):
            out.append({"key": key, "heading": item["title"], "detail": item["detail"],
                        "when": item["last_seen_at"]})
    for item in resolved:
        out.append({"key": f"treasurer:{item['fingerprint']}", "resolved": True})
    return out
