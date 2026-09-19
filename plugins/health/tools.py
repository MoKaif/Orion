"""Read-only chat tool over Vitalist's cached reports."""
from __future__ import annotations

import json
from typing import Any

from orion.core import plugin_sdk as orion

from . import store


class HealthSummaryTool(orion.BaseTool):
    name = "health_summary"
    description = "Get Vitalist's latest evidence-backed daily or weekly Perseus health report."
    triggers = ("health summary", "how active", "steps", "sleep", "heart rate", "bpm",
                "workouts", "fitness", "recovery", "vitalist")
    args_schema = {"period": "daily | weekly"}

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        scope = "weekly" if str(args.get("period", "daily")).lower() in {"week", "weekly"} else "daily"
        c = store.conn()
        try:
            report = store.latest(c, scope)
        finally:
            c.close()
        if report is None:
            return orion.ToolResult(False, f"Vitalist has no {scope} report yet. Run its {scope} pass first.")
        return orion.ToolResult(True, json.dumps(report, indent=2, default=str),
                                {"report_id": report["id"], "scope": scope})
