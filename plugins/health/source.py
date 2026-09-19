"""Read-only Perseus HTTP client.

Perseus remains the source of truth. Vitalist receives the same display-ready daily series as
the Perseus UI and never connects to its PostgreSQL database directly.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from typing import Any

import httpx

from orion.core.config import config


def settings() -> dict[str, Any]:
    return config.section("vitalist")


def base_url() -> str:
    cfg = settings().get("perseus", {})
    return (os.getenv("PERSEUS_BASE_URL") or str(cfg.get("base_url", ""))).rstrip("/")


async def collect(*, today: date | None = None) -> dict[str, Any]:
    cfg = settings()
    url = base_url()
    if not url:
        raise RuntimeError("PERSEUS_BASE_URL is not configured")
    today = today or date.today()
    days = max(30, int(cfg.get("analysis", {}).get("history_days", 30)))
    start = today - timedelta(days=days)
    timeout = float(cfg.get("perseus", {}).get("timeout_seconds", 12))
    headers = {"Accept": "application/json"}
    token = os.getenv("PERSEUS_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(base_url=url, timeout=timeout, headers=headers) as client:
        requests = (
            client.get("/api/v1/metrics/latest", params={
                "range": "30d", "timezone": cfg.get("timezone", "Asia/Kolkata")}),
            client.get("/api/v1/sleep", params={"start": start.isoformat(),
                                                "end": today.isoformat()}),
            client.get("/api/v1/workouts", params={"start": start.isoformat(),
                                                   "end": today.isoformat()}),
        )
        front, sleep, workouts = await asyncio.gather(*requests)
        for response in (front, sleep, workouts):
            response.raise_for_status()
    sleep_data = sleep.json() or {}
    return {
        "front_page": front.json() or {},
        "sleep": {"sessions": sleep_data.get("sessions") or [],
                  "stages": sleep_data.get("stages") or []},
        "workouts": workouts.json() or [],
        "source": url,
    }
