"""Small, explainable personal models for Treasurer.

scikit-learn is optional. With enough history a pair of quantile gradient-boosted models learns
an expected weekly spending interval from seasonality and recent behaviour. Isolation Forest
flags transaction sizes that are unusual within their own category. Missing dependencies or
thin data return ``None`` and the analyzer uses its robust median/MAD baseline instead.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

MODEL_VERSION = "treasurer-gbr-1"


def _sklearn():
    try:
        from sklearn.ensemble import GradientBoostingRegressor, IsolationForest
        from sklearn.metrics import mean_absolute_error
        return GradientBoostingRegressor, IsolationForest, mean_absolute_error
    except Exception:
        return None


def weekly_forecast(daily: dict[date, float], anchor: date, *, minimum_samples: int = 70,
                    random_state: int = 42) -> dict[str, Any] | None:
    deps = _sklearn()
    if deps is None or not daily:
        return None
    GradientBoostingRegressor, _, mean_absolute_error = deps
    first = min(daily)
    days = (anchor - first).days
    if days < minimum_samples:
        return None

    full = {first + timedelta(days=i): float(daily.get(first + timedelta(days=i), 0.0))
            for i in range(days + 1)}
    ordered = sorted(full)
    values = [full[d] for d in ordered]
    features, targets = [], []
    # The target is the seven days ending on d. Features only look behind that window, which
    # prevents the model from learning from the period it is asked to predict.
    for i in range(35, len(ordered) - 7):
        d = ordered[i + 6]
        previous_7 = sum(values[i - 7:i])
        previous_28 = sum(values[i - 28:i]) / 4
        angle = 2 * math.pi * d.timetuple().tm_yday / 365.25
        features.append([
            math.sin(angle), math.cos(angle), d.month, d.day / 31,
            previous_7, previous_28,
        ])
        targets.append(sum(values[i:i + 7]))
    if len(targets) < 30:
        return None

    split = max(20, int(len(targets) * 0.8))
    split = min(split, len(targets) - 8)
    train_x, train_y = features[:split], targets[:split]
    test_x, test_y = features[split:], targets[split:]
    common = dict(n_estimators=120, max_depth=2, min_samples_leaf=5,
                  learning_rate=0.04, random_state=random_state)
    median_model = GradientBoostingRegressor(loss="huber", **common).fit(train_x, train_y)
    low_model = GradientBoostingRegressor(loss="quantile", alpha=0.1, **common).fit(train_x, train_y)
    high_model = GradientBoostingRegressor(loss="quantile", alpha=0.9, **common).fit(train_x, train_y)
    mae = float(mean_absolute_error(test_y, median_model.predict(test_x)))

    # Refit using all completed historical windows, never the current seven-day target.
    median_model.fit(features, targets)
    low_model.fit(features, targets)
    high_model.fit(features, targets)
    current_start = anchor - timedelta(days=6)
    previous = [float(daily.get(current_start - timedelta(days=i), 0.0)) for i in range(1, 29)]
    angle = 2 * math.pi * anchor.timetuple().tm_yday / 365.25
    x = [[math.sin(angle), math.cos(angle), anchor.month, anchor.day / 31,
          sum(previous[:7]), sum(previous) / 4]]
    expected = max(0.0, float(median_model.predict(x)[0]))
    low = max(0.0, float(low_model.predict(x)[0]))
    high = max(expected, float(high_model.predict(x)[0]), expected + mae * 0.5)
    return {
        "method": "ml_quantile_gradient_boosting",
        "version": MODEL_VERSION,
        "expected": round(expected, 2),
        "low": round(min(low, expected), 2),
        "high": round(high, 2),
        "samples": len(targets),
        "validation_mae": round(mae, 2),
        "trained_through": (current_start - timedelta(days=1)).isoformat(),
    }


def unusual_transactions(rows: list[dict[str, Any]], *, minimum_amount: float,
                         random_state: int = 42) -> list[dict[str, Any]]:
    deps = _sklearn()
    if deps is None:
        return []
    _, IsolationForest, _ = deps
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("flow") == "spending":
            by_category.setdefault(str(row.get("category") or "Uncategorised"), []).append(row)
    out: list[dict[str, Any]] = []
    for category, items in by_category.items():
        if len(items) < 30:
            continue
        amounts = [float(i["magnitude"]) for i in items]
        model = IsolationForest(n_estimators=100, contamination="auto",
                                random_state=random_state).fit([[math.log1p(v)] for v in amounts])
        scores = model.decision_function([[math.log1p(v)] for v in amounts])
        cutoff = sorted(amounts)[max(0, int(len(amounts) * 0.90) - 1)]
        for item, score in zip(items, scores):
            if item.get("recent") and item["magnitude"] >= max(minimum_amount, cutoff) and score < 0:
                out.append({**item, "anomaly_score": round(float(-score), 4),
                            "category_samples": len(items), "category_p90": round(cutoff, 2)})
    return sorted(out, key=lambda i: (i["anomaly_score"], i["magnitude"]), reverse=True)[:4]


def model_metrics(forecast: dict[str, Any] | None) -> dict[str, Any]:
    if forecast is None:
        return {"available": False, "reason": "dependency unavailable or insufficient history"}
    return {"available": True, "expected": forecast["expected"],
            "interval": [forecast["low"], forecast["high"]],
            "validation_mae": forecast["validation_mae"]}
