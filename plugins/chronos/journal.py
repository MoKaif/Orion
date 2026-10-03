"""Bounded, read-only source for explicitly approved Obsidian journal folders."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from orion.core.config import config

_DATE = re.compile(r"(\d{4}-\d{1,2}-\d{1,2})")


def _entry_date(path: Path) -> date | None:
    match = _DATE.search(path.stem)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def fetch() -> list[dict[str, str]]:
    """Read recent Markdown entries only from configured, vault-relative folders.

    The allowlist is the approval boundary: Chronos never wanders through the rest of the
    vault. Raw note text is returned to the in-process extractor and is never stored in its DB.
    """
    cfg = config.section("chronos").get("journal", {})
    if not cfg.get("enabled", True):
        return []
    raw_vault = config.section("settings").get("vault", {}).get("path", "")
    if not raw_vault:
        raise RuntimeError("Obsidian vault is not configured")
    vault = Path(str(raw_vault)).expanduser().resolve()
    if not vault.is_dir():
        raise RuntimeError("configured Obsidian vault is unavailable")

    roots = cfg.get("approved_paths", ["Journal"])
    if not isinstance(roots, list) or not roots:
        return []
    lookback = max(1, min(366, int(cfg.get("lookback_days", 45))))
    max_notes = max(1, min(500, int(cfg.get("max_notes", 90))))
    max_chars = max(1000, min(50_000, int(cfg.get("max_chars", 12_000))))
    cutoff = date.today() - timedelta(days=lookback)
    candidates: list[tuple[date, Path]] = []

    for value in roots:
        root = (vault / str(value)).resolve()
        if not root.is_relative_to(vault):
            raise RuntimeError(f"journal path escapes the configured vault: {value}")
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(vault).parts):
                continue
            # A symlink inside Journal must not turn the approved folder into a route elsewhere.
            if not path.resolve().is_relative_to(root):
                continue
            entry_day = _entry_date(path)
            if entry_day is None:
                entry_day = datetime.fromtimestamp(path.stat().st_mtime).date()
            if entry_day >= cutoff:
                candidates.append((entry_day, path))

    out = []
    ordered = sorted(candidates, key=lambda item: (item[0], str(item[1])), reverse=True)
    for entry_day, path in ordered[:max_notes]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:max_chars].strip()
        except OSError:
            continue
        if not text:
            continue
        rel = str(path.relative_to(vault))
        fingerprint = hashlib.sha256(f"{rel}\0{text}".encode("utf-8")).hexdigest()
        out.append({
            "fingerprint": fingerprint,
            "from": "Obsidian Journal",
            "subject": path.stem,
            "date": entry_day.isoformat(),
            "text": text,
            "path": rel,
        })
    return out
