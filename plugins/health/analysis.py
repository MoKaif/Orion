"""Deterministic health facts supplied to Vitalist's prose layer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any

CORE_METRICS = (
    "step_count", "distance_walking_running", "active_energy", "heart_rate",
)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _metric_days(front: dict[str, Any]) -> dict[str, dict[date, float]]:
    try:
        start = date.fromisoformat(str(front["window_start"])[:10])
    except (KeyError, ValueError):
        return {}
    out: dict[str, dict[date, float]] = {}
    for metric in front.get("metrics") or []:
        name = str(metric.get("metric_name") or "")
        multiplier = _number(metric.get("multiplier")) or 1.0
        values = {}
        for index, raw in enumerate(metric.get("series") or []):
            value = _number(raw)
            if value is not None:
                values[start + timedelta(days=index)] = value * multiplier
        out[name] = values
    return out


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline in (None, 0):
        return None
    return round((current - baseline) / abs(baseline), 4)


def _window(series: dict[date, float], start: date, end: date) -> list[float]:
    return [series[d] for d in sorted(series) if start <= d < end]


def _metric_summary(front: dict[str, Any], scope: str, today: date) -> list[dict[str, Any]]:
    by_name = _metric_days(front)
    metadata = {m.get("metric_name"): m for m in (front.get("metrics") or [])}
    rows = []
    if scope == "daily":
        current_start, current_end = today - timedelta(days=1), today
        baseline_start, baseline_end = current_start - timedelta(days=14), current_start
    else:
        current_start, current_end = today - timedelta(days=7), today
        baseline_start, baseline_end = current_start - timedelta(days=7), current_start
    for name in CORE_METRICS:
        series = by_name.get(name, {})
        current_values = _window(series, current_start, current_end)
        baseline_values = _window(series, baseline_start, baseline_end)
        current = _avg(current_values)
        baseline = _avg(baseline_values)
        meta = metadata.get(name, {})
        rows.append({
            "name": name, "label": meta.get("label") or name.replace("_", " ").title(),
            "unit": meta.get("unit") or "", "current": current, "baseline": baseline,
            "change_pct": _change(current, baseline), "current_days": len(current_values),
            "baseline_days": len(baseline_values),
        })
    return rows


def _sleep(raw: dict[str, Any], scope: str, today: date) -> dict[str, Any]:
    current_days = 1 if scope == "daily" else 7
    prior_days = 14 if scope == "daily" else 7
    cut = today - timedelta(days=current_days)
    prior_cut = cut - timedelta(days=prior_days)
    current, prior = [], []
    for session in raw.get("sessions") or []:
        try:
            day = date.fromisoformat(str(session.get("Date"))[:10])
        except (TypeError, ValueError):
            continue
        hours = _number(session.get("Asleep")) or _number(session.get("TotalSleep"))
        if hours is None:
            continue
        if cut <= day < today:
            current.append(hours)
        elif prior_cut <= day < cut:
            prior.append(hours)
    current_avg, prior_avg = _avg(current), _avg(prior)
    return {"average_hours": current_avg, "baseline_hours": prior_avg,
            "change_pct": _change(current_avg, prior_avg), "nights": len(current),
            "baseline_nights": len(prior)}


def _workouts(raw: list[dict[str, Any]], scope: str, today: date) -> dict[str, Any]:
    start = today - timedelta(days=1 if scope == "daily" else 7)
    selected = []
    for workout in raw:
        try:
            day = date.fromisoformat(str(workout.get("StartTime"))[:10])
        except (TypeError, ValueError):
            continue
        if start <= day < today:
            selected.append(workout)
    seconds = sum(_number(w.get("DurationSec")) or 0 for w in selected)
    return {"count": len(selected), "minutes": round(seconds / 60),
            "types": sorted({str(w.get("Name")) for w in selected if w.get("Name")})}


def _findings(metrics: list[dict[str, Any]], sleep: dict[str, Any], scope: str) -> list[str]:
    found = []
    for item in metrics:
        value, delta = item["current"], item["change_pct"]
        if value is None:
            continue
        if delta is not None and abs(delta) >= .15:
            direction = "above" if delta > 0 else "below"
            found.append(f"{item['label']} was {abs(delta):.0%} {direction} its comparison baseline.")
    if sleep["average_hours"] is not None and sleep["change_pct"] is not None \
            and abs(sleep["change_pct"]) >= .10:
        direction = "longer" if sleep["change_pct"] > 0 else "shorter"
        found.append(f"Recorded sleep was {abs(sleep['change_pct']):.0%} {direction} than its baseline.")
    if not found:
        found.append(f"No material change was detected in the available {scope} health data.")
    return found[:5]


def build(raw: dict[str, Any], scope: str, *, today: date | None = None) -> dict[str, Any]:
    if scope not in {"daily", "weekly"}:
        raise ValueError("scope must be daily or weekly")
    today = today or date.today()
    metrics = _metric_summary(raw.get("front_page") or {}, scope, today)
    sleep = _sleep(raw.get("sleep") or {}, scope, today)
    workouts = _workouts(raw.get("workouts") or [], scope, today)
    complete = [m["name"] for m in metrics if m["current"] is not None]
    return {
        "scope": scope, "period_end": today.isoformat(),
        "period_start": (today - timedelta(days=1 if scope == "daily" else 7)).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Perseus", "source_last_sync": (raw.get("front_page") or {}).get("last_sync"),
        "available_metrics": complete, "metrics": metrics, "sleep": sleep,
        "workouts": workouts, "findings": _findings(metrics, sleep, scope),
    }
