"""Read-only FinStrive connector.

The current FinStrive API exposes transactions over localhost. Treasurer never calls its write,
sync, or reconciliation routes. A future aggregate endpoint can replace this adapter without
changing the inference pipeline.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from orion.core.config import config


def settings() -> dict[str, Any]:
    return config.section("treasurer")


async def transactions() -> list[dict[str, Any]]:
    cfg = settings().get("finstrive", {})
    base = str(cfg.get("base_url", "http://127.0.0.1:5101")).rstrip("/")
    path = str(cfg.get("transactions_path", "/api/transactions"))
    headers = {"Accept": "application/json"}
    token = os.environ.get(str(cfg.get("token_env", "FINSTRIVE_TREASURER_TOKEN")), "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = float(cfg.get("timeout_seconds", 20))
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.get(f"{base}{path}")
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        raise ValueError("FinStrive transaction endpoint returned a non-list response")
    return [row for row in data if isinstance(row, dict)]


async def available() -> tuple[bool, str]:
    try:
        rows = await transactions()
        return True, f"{len(rows)} transactions readable"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
