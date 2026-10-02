from __future__ import annotations

import json
from typing import Any

from orion.core import plugin_sdk as orion
from . import store


class UpcomingScheduleTool(orion.BaseTool):
    name = "upcoming_schedule"
    description = "Get confirmed upcoming Google Calendar events and pending Chronos proposals."
    triggers = ("calendar", "schedule", "upcoming", "this week", "this month", "tomorrow",
                "appointment", "meeting", "deadline", "chronos")
    args_schema = {"limit": "maximum events, 1-30"}

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        try:
            limit = max(1, min(30, int(args.get("limit", 15))))
        except (TypeError, ValueError):
            limit = 15
        c = store.conn()
        try:
            data = {"confirmed": store.upcoming(c, limit),
                    "awaiting_approval": store.proposals(c, limit=limit)}
        finally:
            c.close()
        return orion.ToolResult(True, json.dumps(data, default=str, indent=2),
                                {"confirmed": len(data["confirmed"]),
                                 "pending": len(data["awaiting_approval"])})
