"""Obsidian vault ingestion.

Walks the configured vault, upserts each note as a ``note`` entity, stores its text as accepted
knowledge (source = the file path), and indexes it for semantic recall. Idempotent: re-running
updates existing notes by canonical key rather than duplicating.

Deliberately modest for M1: notes become searchable knowledge. Concept extraction and
note<->note relationships build on top of this in later milestones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orion.core.config import config
from orion.core.world_model import world_model

_MAX_CHARS = 8000  # cap per note so a giant file can't bloat a single knowledge row


def _vault_path() -> Path | None:
    raw = config.section("settings").get("vault", {}).get("path", "")
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None


def ingest_vault() -> dict[str, Any]:
    vault = _vault_path()
    if vault is None:
        return {"ok": False, "reason": "no vault configured (settings.json > vault.path)"}

    exts = tuple(config.section("settings").get("vault", {}).get("extensions", [".md"]))
    ingested, skipped = 0, 0
    for path in vault.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            skipped += 1
            continue
        if not text:
            skipped += 1
            continue
        rel = str(path.relative_to(vault))
        eid = world_model.upsert_entity("note", path.stem, canonical_key=f"note:{rel}",
                                        source=rel)
        world_model.add_knowledge(eid, key="content", value=text[:_MAX_CHARS],
                                  kind="fact", confidence=1.0, status="accepted", source=rel)
        ingested += 1

    world_model.add_event("vault_ingested", {"ingested": ingested, "skipped": skipped})
    return {"ok": True, "ingested": ingested, "skipped": skipped, "vault": str(vault)}
