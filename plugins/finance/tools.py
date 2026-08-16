"""Read-only chat tools over Treasurer's latest derived snapshot."""
from __future__ import annotations

import json
from typing import Any

from orion.core import plugin_sdk as orion

from . import store


def _latest() -> dict[str, Any] | None:
    c = store.conn()
    try:
        return store.latest_snapshot(c)
    finally:
        c.close()


class FinanceSummaryTool(orion.BaseTool):
    name = "finance_summary"
    description = "Get computed income, spending, investing, refunds, and net figures from FinStrive."
    triggers = ("how much did i spend", "finance summary", "money in", "income", "cash flow",
                "savings rate", "spent this", "invested")
    args_schema = {"period": "last_7d | previous_7d | month_to_date | previous_month"}

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        snap = _latest()
        if snap is None:
            return orion.ToolResult(False, "Treasurer has no snapshot yet. Run its analysis pass first.")
        period = str(args.get("period") or "month_to_date").lower().replace(" ", "_")
        aliases = {"week": "last_7d", "this_week": "last_7d", "month": "month_to_date",
                   "this_month": "month_to_date", "last_month": "previous_month"}
        period = aliases.get(period, period)
        figures = snap.get("periods", {}).get(period)
        if figures is None:
            return orion.ToolResult(False, f"Unknown period {period!r}.")
        return orion.ToolResult(True, json.dumps({"period": period, "as_of": snap["as_of"],
                                                  "figures": figures}, indent=2),
                                {"snapshot_id": snap.get("snapshot_id")})


class SpendingBreakdownTool(orion.BaseTool):
    name = "spending_breakdown"
    description = "Break personal spending down by category for the last seven days or month to date."
    triggers = ("spending breakdown", "where did my money go", "category", "categories",
                "dining", "transport", "shopping")
    args_schema = {"period": "last_7d | month_to_date", "category": "optional category name"}

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        snap = _latest()
        if snap is None:
            return orion.ToolResult(False, "Treasurer has no snapshot yet.")
        period = str(args.get("period") or "month_to_date").lower().replace(" ", "_")
        key = "categories_last_7d" if period in {"week", "last_7d", "this_week"} else "categories_month_to_date"
        values = snap.get(key, {})
        wanted = str(args.get("category") or "").strip().lower()
        if wanted:
            values = {k: v for k, v in values.items() if wanted in k.lower()}
        ranked = sorted(values.items(), key=lambda x: x[1], reverse=True)
        return orion.ToolResult(True, json.dumps({"period": key.removeprefix("categories_"),
                                                  "categories": ranked}, indent=2))


class FinanceInsightsTool(orion.BaseTool):
    name = "finance_insights"
    description = "Get Treasurer's current ML anomalies and LLM interpretations with evidence and confidence."
    triggers = ("financial insight", "spending trend", "unusual", "anomaly", "why am i spending",
                "treasurer found", "what changed")
    args_schema = {"limit": "maximum insights, default 6"}

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        try:
            limit = max(1, min(20, int(args.get("limit", 6))))
        except (TypeError, ValueError):
            limit = 6
        c = store.conn()
        try:
            items = store.insights(c, limit=limit)
        finally:
            c.close()
        return orion.ToolResult(True, json.dumps(items, indent=2, default=str), {"count": len(items)})
