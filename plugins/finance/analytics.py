"""Financial semantics, feature generation, and evidence-backed insight detection."""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable

from . import models


def _pick(row: dict[str, Any], camel: str, pascal: str, default=None):
    return row.get(camel, row.get(pascal, default))


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return out.replace(tzinfo=timezone.utc) if out.tzinfo is None else out
    except (TypeError, ValueError):
        return None


def account_kind(account: Any) -> str:
    value = str(account or "").strip().lower()
    if value.startswith("assets:banking"):
        return "cash"
    if value.startswith("assets:investment"):
        return "investment"
    if value.startswith("expenses:") or value.startswith("expense:"):
        return "expense"
    if value == "income" or value.startswith("income:"):
        return "income"
    return "other"


def flow_kind(source: str, destination: str) -> str:
    if source == "cash" and destination == "cash":
        return "internal"
    if destination == "investment":
        return "investing"
    if source == "investment" and destination == "cash":
        return "divestment"
    if source == "income":
        return "income"
    if source == "expense" and destination == "cash":
        return "refund"
    if source == "cash" and destination == "expense":
        return "spending"
    if source == "cash":
        return "spending"
    if destination == "cash":
        return "income"
    return "unknown"


_NOISE = re.compile(r"\b(?:upi|imps|neft|ref|txn|payment|purchase|debit|credit)\b|\d{4,}", re.I)
_SPACE = re.compile(r"[^a-z0-9]+", re.I)


def merchant(row: dict[str, Any]) -> str:
    raw = str(_pick(row, "descriptionClean", "DescriptionClean") or
              _pick(row, "descriptionRaw", "DescriptionRaw") or "Unknown")
    cleaned = _SPACE.sub(" ", _NOISE.sub(" ", raw)).strip()
    return " ".join(cleaned.split()[:5]).title() or "Unknown"


def classify(raw: dict[str, Any], anchor: date) -> dict[str, Any] | None:
    if bool(_pick(raw, "skipped", "Skipped", False)):
        return None
    when = _date(_pick(raw, "txnDate", "TxnDate"))
    if when is None:
        return None
    source = account_kind(_pick(raw, "accountFrom", "AccountFrom"))
    destination = account_kind(_pick(raw, "accountTo", "AccountTo"))
    flow = flow_kind(source, destination)
    try:
        magnitude = abs(float(_pick(raw, "amount", "Amount", 0) or 0))
    except (TypeError, ValueError):
        magnitude = 0.0
    account = (_pick(raw, "accountTo", "AccountTo") if flow == "spending" else
               _pick(raw, "accountFrom", "AccountFrom") if flow == "refund" else None)
    leaf = str(account or "").split(":")[-1].strip()
    category = leaf or str(_pick(raw, "category", "Category") or "Uncategorised")
    return {
        "id": int(_pick(raw, "id", "Id", 0) or 0),
        "date": when,
        "magnitude": round(magnitude, 2),
        "flow": flow,
        "category": category,
        "merchant": merchant(raw),
        "mapped": bool(_pick(raw, "mapped", "Mapped", False)),
        "created_at": _stamp(_pick(raw, "createdAt", "CreatedAt")),
        "recent": anchor - timedelta(days=6) <= when <= anchor,
    }


def _totals(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    sums: Counter = Counter()
    counts: Counter = Counter()
    for row in rows:
        sums[row["flow"]] += row["magnitude"]
        counts[row["flow"]] += 1
    spending = sums["spending"] - sums["refund"]
    income = sums["income"]
    return {
        "income": round(income, 2), "spending": round(spending, 2),
        "investing": round(sums["investing"], 2), "refunds": round(sums["refund"], 2),
        "divestments": round(sums["divestment"], 2), "internal": round(sums["internal"], 2),
        "unknown": round(sums["unknown"], 2), "net": round(income - spending, 2),
        "savings_rate": round(sums["investing"] / income * 100, 1) if income else None,
        "counts": dict(counts),
    }


def _period(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    return _totals(r for r in rows if start <= r["date"] <= end)


def _categories(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    out: Counter = Counter()
    for row in rows:
        if row["flow"] == "spending":
            out[row["category"]] += row["magnitude"]
        elif row["flow"] == "refund":
            out[row["category"]] -= row["magnitude"]
    return {k: round(v, 2) for k, v in out.items() if v > 0}


def _recurring(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["flow"] == "spending" and row["merchant"] != "Unknown":
            groups[row["merchant"]].append(row)
    found = []
    for name, items in groups.items():
        items.sort(key=lambda r: r["date"])
        if len(items) < 3:
            continue
        intervals = [(b["date"] - a["date"]).days for a, b in zip(items, items[1:])]
        monthly = [i for i in intervals[-5:] if 24 <= i <= 38]
        amounts = [i["magnitude"] for i in items[-6:]]
        center = median(amounts)
        if len(monthly) >= 2 and center > 0 and max(abs(v - center) for v in amounts) / center <= .25:
            found.append({"merchant": name, "amount": round(center, 2),
                          "last_seen": items[-1]["date"].isoformat(), "payments": len(items)})
    return sorted(found, key=lambda r: r["amount"], reverse=True)[:15]


def snapshot(raw_rows: list[dict[str, Any]], cfg: dict[str, Any], *,
             anchor: date | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    anchor = anchor or date.today()
    history_days = int(cfg.get("analysis", {}).get("history_days", 400))
    cutoff = anchor - timedelta(days=history_days)
    rows = [r for raw in raw_rows if (r := classify(raw, anchor)) is not None and r["date"] >= cutoff]
    rows.sort(key=lambda r: (r["date"], r["id"]))
    current_start = anchor - timedelta(days=6)
    previous_start = current_start - timedelta(days=7)
    month_start = anchor.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)

    daily: Counter = Counter()
    for row in rows:
        if row["flow"] == "spending":
            daily[row["date"]] += row["magnitude"]
        elif row["flow"] == "refund":
            daily[row["date"]] -= row["magnitude"]
    monthly = []
    cursor = (anchor.replace(day=1) - timedelta(days=330)).replace(day=1)
    while cursor <= month_start:
        end = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        monthly.append({"month": cursor.strftime("%Y-%m"), **_period(rows, cursor, min(end, anchor))})
        cursor = end + timedelta(days=1)

    created = [r["created_at"] for r in rows if r["created_at"]]
    quality = {
        "unmapped_count": sum(not r["mapped"] for r in rows),
        "unknown_flow_count": sum(r["flow"] == "unknown" for r in rows),
        "unknown_flow_ratio": round(sum(r["flow"] == "unknown" for r in rows) / len(rows), 4) if rows else 0,
    }
    payload = {
        "schema_version": 1,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anchor_date": anchor.isoformat(),
        "latest_transaction_at": max((r["date"] for r in rows), default=None).isoformat() if rows else None,
        "latest_import_at": max(created).isoformat(timespec="seconds") if created else None,
        "transaction_count": len(rows),
        "quality": quality,
        "periods": {
            "last_7d": _period(rows, current_start, anchor),
            "previous_7d": _period(rows, previous_start, current_start - timedelta(days=1)),
            "month_to_date": _period(rows, month_start, anchor),
            "previous_month": _period(rows, prev_start, prev_end),
        },
        "categories_last_7d": _categories(r for r in rows if current_start <= r["date"] <= anchor),
        "categories_month_to_date": _categories(r for r in rows if month_start <= r["date"] <= anchor),
        "monthly": monthly,
        "daily_spending": [{"date": d.isoformat(), "spending": round(v, 2)} for d, v in sorted(daily.items())],
        "recurring": _recurring(rows),
        "top_recent_transactions": [
            {k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()
             if k in {"id", "date", "magnitude", "category", "merchant", "flow"}}
            for r in sorted((x for x in rows if x["recent"] and x["flow"] == "spending"),
                            key=lambda x: x["magnitude"], reverse=True)[:12]
        ],
    }
    return payload, rows


def _mad(values: list[float], center: float) -> float:
    return median([abs(v - center) for v in values]) if values else 0.0


def _money(value: float) -> str:
    return f"₹{value:,.0f}"


def detect(payload: dict[str, Any], rows: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    acfg = cfg.get("analysis", {})
    mcfg = cfg.get("ml", {})
    anchor = date.fromisoformat(payload["anchor_date"])
    current_start = anchor - timedelta(days=6)
    baseline_weeks = int(acfg.get("baseline_weeks", 10))
    minimum_weeks = int(acfg.get("minimum_baseline_weeks", 4))
    spike_ratio = float(acfg.get("spike_ratio", 1.35))
    critical_ratio = float(acfg.get("critical_spike_ratio", 1.75))
    min_spend = float(acfg.get("minimum_category_spend_inr", 1000))
    min_change = float(acfg.get("minimum_change_inr", 750))
    current_rows = [r for r in rows if current_start <= r["date"] <= anchor]
    current_total = float(payload["periods"]["last_7d"]["spending"])
    weekly_totals, weekly_categories = [], []
    for offset in range(1, baseline_weeks + 1):
        end = current_start - timedelta(days=1 + 7 * (offset - 1))
        start = end - timedelta(days=6)
        sample = [r for r in rows if start <= r["date"] <= end]
        weekly_totals.append(float(_totals(sample)["spending"]))
        weekly_categories.append(_categories(sample))

    daily = {date.fromisoformat(i["date"]): float(i["spending"])
             for i in payload.get("daily_spending", [])}
    forecast = None
    if mcfg.get("enabled", True):
        forecast = models.weekly_forecast(
            daily, anchor, minimum_samples=int(mcfg.get("minimum_daily_samples", 70)),
            random_state=int(mcfg.get("random_state", 42)))

    found: list[dict[str, Any]] = []
    if len(weekly_totals) >= minimum_weeks:
        center = median(weekly_totals)
        mad = _mad(weekly_totals, center)
        high = center + max(3 * mad, center * .25, min_change)
        method = "robust_weekly_median_mad"
        expected = center
        if forecast:
            high, expected, method = forecast["high"], forecast["expected"], forecast["method"]
        # A zero baseline means "new spending", not an IEEE infinity. Evidence crosses a JSON
        # boundary into the agent page and Herald, so every numeric claim must remain finite.
        ratio = current_total / expected if expected > 0 else current_total / max(min_change, 1)
        if current_total >= max(high, expected * spike_ratio) and current_total - expected >= min_change:
            confidence = min(.97, .62 + min(.25, len(weekly_totals) / 40) + min(.1, (ratio - 1) / 4))
            found.append({
                "fingerprint": "spending_spike:overall", "kind": "spending_spike", "scope": "overall",
                "title": "Weekly spending is above its expected range",
                "detail": f"You spent {_money(current_total)} in seven days; the expected level was {_money(expected)}.",
                "evidence": {"observed": current_total, "expected": round(expected, 2),
                             "expected_high": round(high, 2), "ratio": round(ratio, 3),
                             "baseline_weeks": len(weekly_totals), "period": [current_start.isoformat(), anchor.isoformat()]},
                "confidence": round(confidence, 3),
                "severity": "critical" if ratio >= critical_ratio else "attention", "method": method,
            })

    current_categories = _categories(current_rows)
    for category, observed in sorted(current_categories.items(), key=lambda x: x[1], reverse=True):
        historical = [float(w.get(category, 0)) for w in weekly_categories]
        if len(historical) < minimum_weeks:
            continue
        center = median(historical)
        high = center + max(3 * _mad(historical, center), center * .3, min_change)
        ratio = observed / center if center > 0 else observed / max(min_change, 1)
        if observed >= min_spend and observed >= high and observed - center >= min_change and ratio >= spike_ratio:
            found.append({
                "fingerprint": f"category_spike:{hashlib.sha1(category.lower().encode()).hexdigest()[:12]}",
                "kind": "category_spike", "scope": category,
                "title": f"{category} spending is unusually high",
                "detail": f"{category} reached {_money(observed)}, compared with a typical {_money(center)} week.",
                "evidence": {"category": category, "observed": observed, "baseline_median": round(center, 2),
                             "expected_high": round(high, 2), "ratio": round(ratio, 3),
                             "baseline_weeks": len(historical), "transaction_count": sum(r["category"] == category and r["flow"] == "spending" for r in current_rows)},
                "confidence": round(min(.94, .6 + len(historical) / 50 + min(.12, (ratio - 1) / 5)), 3),
                "severity": "critical" if ratio >= critical_ratio and observed >= min_spend * 2 else "attention",
                "method": "category_median_mad",
            })

    if mcfg.get("enabled", True):
        unusual = models.unusual_transactions(
            rows, minimum_amount=float(acfg.get("unusual_transaction_inr", 2500)),
            random_state=int(mcfg.get("random_state", 42)))
        for item in unusual:
            found.append({
                "fingerprint": f"unusual_transaction:{item['id']}", "kind": "unusual_transaction",
                "scope": item["category"], "title": f"An unusual {item['category']} payment appeared",
                "detail": f"A {_money(item['magnitude'])} payment is unusual for this category.",
                "evidence": {"transaction_id": item["id"], "date": item["date"].isoformat(),
                             "amount": item["magnitude"], "category": item["category"],
                             "merchant": item["merchant"], "category_p90": item["category_p90"],
                             "category_samples": item["category_samples"], "anomaly_score": item["anomaly_score"]},
                "confidence": round(min(.93, .7 + item["anomaly_score"]), 3),
                "severity": "attention", "method": "isolation_forest_by_category",
            })

    quality = payload["quality"]
    if quality["unmapped_count"]:
        found.append({
            "fingerprint": "data_quality:unmapped", "kind": "data_quality", "scope": "mapping",
            "title": "Some transactions are not categorized yet",
            "detail": f"{quality['unmapped_count']} transactions are unmapped, so category findings are provisional.",
            "evidence": {"unmapped_count": quality["unmapped_count"]}, "confidence": 1.0,
            "severity": "notice", "method": "data_quality_rule",
        })
    if quality["unknown_flow_ratio"] >= float(acfg.get("unknown_flow_ratio", .05)):
        found.append({
            "fingerprint": "data_quality:unknown_flow", "kind": "data_quality", "scope": "classification",
            "title": "Too many transactions have an unknown flow",
            "detail": f"{quality['unknown_flow_count']} transactions cannot be classified reliably.",
            "evidence": quality, "confidence": 1.0, "severity": "attention", "method": "data_quality_rule",
        })
    latest_import = _stamp(payload.get("latest_import_at"))
    stale_hours = float(acfg.get("stale_after_hours", 96))
    age = ((datetime.now(timezone.utc) - latest_import).total_seconds() / 3600) if latest_import else None
    if payload["transaction_count"] and (age is None or age > stale_hours):
        age_text = f"{age / 24:.1f} days" if age is not None else "an unknown period"
        found.append({
            "fingerprint": "data_quality:stale", "kind": "data_stale", "scope": "FinStrive",
            "title": "FinStrive data may be stale", "detail": f"No transaction import has been recorded for {age_text}.",
            "evidence": {"latest_import_at": payload.get("latest_import_at"),
                         "age_hours": round(age, 1) if age is not None else None},
            "confidence": 1.0, "severity": "attention", "method": "freshness_rule",
        })
    return found[:int(acfg.get("max_insights", 12))], forecast
