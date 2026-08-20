"""Narrow FinStrive connector for Treasurer.

Transaction analysis remains read-only. The sole mutation exposed here is the dedicated mailbox
scan route, which may only create unmapped reconciliation candidates; Treasurer has no general
transaction create, edit, map, skip, or delete capability.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from orion.core.config import config


def settings() -> dict[str, Any]:
    return config.section("treasurer")


def _connection() -> tuple[str, dict[str, str], float, dict[str, Any]]:
    cfg = settings().get("finstrive", {})
    base = str(cfg.get("base_url", "http://127.0.0.1:5101")).rstrip("/")
    headers = {"Accept": "application/json"}
    token = os.environ.get(str(cfg.get("token_env", "FINSTRIVE_TREASURER_TOKEN")), "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = float(cfg.get("timeout_seconds", 20))
    return base, headers, timeout, cfg


async def transactions() -> list[dict[str, Any]]:
    base, headers, timeout, cfg = _connection()
    path = str(cfg.get("transactions_path", "/api/transactions"))
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.get(f"{base}{path}")
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        raise ValueError("FinStrive transaction endpoint returned a non-list response")
    return [row for row in data if isinstance(row, dict)]


async def scan_transaction_emails() -> dict[str, int]:
    """Ask FinStrive to turn recent HDFC alerts into reconciliation candidates."""
    base, headers, timeout, cfg = _connection()
    path = str(cfg.get("mail_sync_path", "/api/transactions/sync-email-transactions"))
    lookback = max(1, min(90, int(cfg.get("mail_sync_lookback_days", 2))))
    mail_timeout = float(cfg.get("mail_sync_timeout_seconds", max(timeout, 120)))
    async with httpx.AsyncClient(timeout=mail_timeout, headers=headers) as client:
        response = await client.post(f"{base}{path}", params={"lookbackDays": lookback})
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("FinStrive mailbox sync returned a non-object response")
    required = ("scanned", "matched", "created", "duplicates", "ignored")
    if any(not isinstance(data.get(key), int) or data[key] < 0 for key in required):
        raise ValueError("FinStrive mailbox sync returned invalid counters")
    return {key: data[key] for key in required}


async def available() -> tuple[bool, str]:
    try:
        rows = await transactions()
        return True, f"{len(rows)} transactions readable"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
