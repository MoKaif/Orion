from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from orion.core import plugin_sdk as orion
from orion.core.config import config
from . import calendar, engine, store


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


class CreateCalendarEventTool(orion.BaseTool):
    name = "create_calendar_event"
    description = "Create a private Google Calendar event from chat after explicit user confirmation."
    triggers = ("add to my calendar", "put on my calendar", "create a calendar event",
                "create calendar event", "create an event", "add a calendar event", "add an event",
                "add a meeting", "add an appointment", "to calendar", "on the calendar",
                "schedule a meeting", "schedule an appointment", "schedule my", "book a")
    args_schema = {
        "title": "required event title",
        "start_at": "required RFC3339 datetime with timezone, or YYYY-MM-DD for all-day",
        "end_at": "optional RFC3339 datetime with timezone, or YYYY-MM-DD",
        "location": "optional location",
        "description": "optional short context from the user",
    }
    requires_confirm = True
    match_priority = 1

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        title = str(args.get("title") or "").strip()[:300]
        start = str(args.get("start_at") or "").strip()
        if not title or not start:
            return orion.ToolResult(False, "A title and start date/time are required.")
        try:
            if "T" in start:
                parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    zone = ZoneInfo(str(config.section("chronos").get("timezone", "Asia/Kolkata")))
                    parsed = parsed.replace(tzinfo=zone)
                start = parsed.isoformat()
                if parsed.date() < datetime.now(parsed.tzinfo).date():
                    raise ValueError("start is in the past")
            else:
                parsed_day = date.fromisoformat(start[:10])
                start = parsed_day.isoformat()
                if parsed_day < date.today():
                    raise ValueError("start is in the past")
        except ValueError as exc:
            return orion.ToolResult(False, f"Invalid event start: {exc}")

        item = {
            "title": title,
            "start_at": start,
            "end_at": str(args.get("end_at") or "").strip() or None,
            "location": str(args.get("location") or "").strip()[:500] or None,
            "description": str(args.get("description") or "Created from Orion chat.").strip()[:1000],
        }
        try:
            created = await asyncio.to_thread(calendar.create_event, item)
            await engine.sync_calendar()
        except Exception as exc:
            return orion.ToolResult(False, f"Calendar event could not be created: {exc}")
        return orion.ToolResult(True, f'Created "{title}" on Google Calendar.', created)
