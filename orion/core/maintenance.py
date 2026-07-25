"""Core background maintenance jobs — Orion's nightly/weekly 'own life'.

These generate reviewable discoveries rather than silently mutating the world model (the
constitution: never silently alter long-term knowledge). Deterministic parts (duplicate
detection, briefing stats) run with zero dependencies; the LLM-assisted inference pass is
skipped gracefully when no local model is available.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from orion.core.world_model import world_model
from orion.core.world_model.extract import extract_candidates

log = logging.getLogger("orion.maintenance")


async def consolidate() -> dict:
    """Nightly: find likely-duplicate entities and re-mine recent conversation for knowledge.

    Everything found is proposed to the Review Inbox for Accept/Edit/Reject.
    """
    proposed = 0

    # 1. duplicate detection (deterministic): same type + case-insensitive same name.
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for e in world_model.entity_index():
        groups[(e["type"], e["name"].strip().lower())].append(e["id"])
    for (etype, name), ids in groups.items():
        if len(ids) > 1:
            world_model.propose("duplicate",
                                {"entity_type": etype, "name": name, "entity_ids": ids}, 0.9)
            proposed += 1

    # 2. inferred knowledge from recent conversation (LLM-assisted; skipped if unavailable).
    msgs = world_model.recent_messages(limit=30)
    if msgs:
        blob = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
        for candidate in await extract_candidates(blob):
            candidate.setdefault("source", "maintenance:consolidate")
            if world_model.ingest_candidate(candidate).get("outcome") == "queued":
                proposed += 1

    world_model.add_event("consolidated", {"proposed": proposed})
    log.info("consolidation proposed %d review item(s)", proposed)
    return {"proposed": proposed}


async def weekly_briefing() -> dict:
    """Weekly: summarize state and surface it as a discovery in the Review Inbox."""
    s = world_model.stats()
    text = (f"Weekly briefing - {s['entities']} entities, {s['knowledge']} knowledge items, "
            f"{s['relationships']} relationships, {s['pending_reviews']} items awaiting review, "
            f"{s['events']} events logged.")
    world_model.propose("discovery", {"kind": "weekly_briefing", "summary": text, "stats": s}, 1.0)
    world_model.add_event("weekly_briefing", s)
    log.info("weekly briefing generated")
    return {"summary": text, "stats": s}


def register_core_jobs() -> None:
    from orion.core.scheduler import scheduler, ScheduledJob, configured_cron
    scheduler.register(ScheduledJob("consolidate",
                                    configured_cron("consolidate", "0 3 * * *"), consolidate))
    scheduler.register(ScheduledJob("weekly_briefing",
                                    configured_cron("weekly_briefing", "0 8 * * 1"), weekly_briefing))
