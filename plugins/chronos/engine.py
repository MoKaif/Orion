"""Chronos jobs and approval-gated calendar mutation."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import calendar, inference, mail, store

log = logging.getLogger("orion.chronos")


async def sync_calendar() -> dict[str, Any]:
    try:
        events = await asyncio.to_thread(calendar.list_events)
        c = store.conn()
        try:
            store.replace_events(c, events)
            store.set_meta(c, "last_calendar_sync", store.now())
            store.set_meta(c, "last_calendar_error", "")
        finally:
            c.close()
        return {"ok": True, "events": len(events)}
    except Exception as exc:
        c = store.conn()
        try:
            store.set_meta(c, "last_calendar_error", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            c.close()
        log.info("Chronos calendar sync unavailable: %s", exc)
        return {"ok": False, "error": str(exc)}


async def scan_mail() -> dict[str, Any]:
    try:
        messages = await asyncio.to_thread(mail.fetch)
    except Exception as exc:
        c = store.conn()
        try:
            store.set_meta(c, "last_mail_error", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            c.close()
        return {"ok": False, "error": str(exc)}
    from orion.core.config import config
    scanned = proposed = 0
    c = store.conn()
    try:
        candidates = []
        for message in messages:
            if store.seen(c, message["fingerprint"]):
                continue
            scanned += 1
            if inference.likely_relevant(message):
                candidates.append(message)
            else:
                store.mark_seen(c, message["fingerprint"])
        batch_size = max(1, min(20, int(config.section("chronos").get("mail", {}).get(
            "inference_batch", 10))))
        batch = candidates[:batch_size]
        extracted = await inference.extract_many(batch)
        if extracted is None:
            store.set_meta(c, "last_mail_error", "Local inference timed out; messages remain pending")
            return {"ok": False, "messages": scanned, "proposals": 0,
                    "deferred": len(candidates), "error": "local inference timed out"}
        for item in extracted:
            item.update(source_kind="mail")
            if item and item["confidence"] >= 0.55:
                proposed += bool(store.add_proposal(c, item))
        for message in batch:
            store.mark_seen(c, message["fingerprint"])
        store.set_meta(c, "last_mail_scan", store.now())
        store.set_meta(c, "last_mail_error", "")
    finally:
        c.close()
    return {"ok": True, "messages": scanned, "proposals": proposed,
            "deferred": max(0, len(candidates) - len(batch))}


async def resolve_proposal(pid: int, action: str) -> dict[str, Any]:
    c = store.conn()
    try:
        item = store.proposal(c, pid)
        if item is None or item["status"] != "pending":
            return {"ok": False, "reason": "unknown or already-resolved proposal"}
        if action == "reject":
            store.resolve(c, pid, "rejected")
            return {"ok": True, "outcome": "rejected"}
    finally:
        c.close()
    if action != "accept":
        return {"ok": False, "reason": "action must be accept or reject"}
    created = await asyncio.to_thread(calendar.create_event, item)
    c = store.conn()
    try:
        store.resolve(c, pid, "accepted", created.get("id"))
    finally:
        c.close()
    await sync_calendar()
    return {"ok": True, "outcome": "created", "event": created}
