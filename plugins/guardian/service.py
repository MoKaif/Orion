"""Model-free protection for Orion's durable local state."""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orion.core.config import config


def settings() -> dict[str, Any]:
    return config.section("guardian")


def backup_root() -> Path:
    return config.root() / "data" / "backups" / "guardian"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(target, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup() -> dict[str, Any]:
    """Create an atomic directory snapshot; a failed run never publishes a partial backup."""
    if not settings().get("enabled", True):
        return {"ok": True, "outcome": "disabled"}
    root = config.root()
    destination = backup_root()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    partial = destination / f".{stamp}.partial"
    final = destination / stamp
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir()

    copied: list[str] = []
    try:
        data_dir = root / "data"
        for source in sorted(data_dir.glob("*.db")):
            _sqlite_backup(source, partial / "data" / source.name)
            copied.append(f"data/{source.name}")
        for source in sorted((root / "config").glob("*.json")):
            if source.name == "secrets.json":
                continue
            target = partial / "config" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(f"config/{source.name}")
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": copied,
            "format": 1,
        }
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        partial.rename(final)
    except Exception:
        if partial.exists():
            shutil.rmtree(partial)
        raise

    removed = _prune(int(settings().get("retention", 7) or 7))
    return {"ok": True, "outcome": "created", "backup": final.name,
            "files": len(copied), "removed": removed}


def _prune(keep: int) -> int:
    root = backup_root().resolve()
    snapshots = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = 0
    for path in snapshots[max(1, keep):]:
        if path.resolve().parent != root:
            continue
        shutil.rmtree(path)
        removed += 1
    return removed


def backups() -> list[dict[str, Any]]:
    root = backup_root()
    if not root.exists():
        return []
    out = []
    for path in sorted(root.iterdir(), reverse=True):
        manifest = path / "manifest.json"
        if not path.is_dir() or path.name.startswith(".") or not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        out.append({"name": path.name, "created_at": data.get("created_at"),
                    "files": len(data.get("files") or []), "bytes": size})
    return out


def audit() -> dict[str, Any]:
    root = config.root()
    problems: list[str] = []
    checked = 0
    for db in sorted((root / "data").glob("*.db")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            verdict = con.execute("PRAGMA quick_check").fetchone()[0]
            con.close()
            checked += 1
            if verdict != "ok":
                problems.append(f"{db.name}: {verdict}")
        except sqlite3.Error as exc:
            problems.append(f"{db.name}: {exc}")

    for cfg in sorted((root / "config").glob("*.json")):
        try:
            json.loads(cfg.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{cfg.name}: invalid configuration ({exc})")

    free_gb = shutil.disk_usage(root).free / (1024 ** 3)
    minimum = float(settings().get("minimum_free_gb", 2.0) or 2.0)
    if free_gb < minimum:
        problems.append(f"disk space low: {free_gb:.1f} GiB free")

    snapshots = backups()
    latest = snapshots[0] if snapshots else None
    stale_hours = None
    if latest and latest.get("created_at"):
        try:
            created = datetime.fromisoformat(latest["created_at"])
            stale_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        except ValueError:
            problems.append("latest backup has an invalid timestamp")
    if latest is None:
        problems.append("no Guardian backup exists yet")
    elif stale_hours is not None and stale_hours > float(settings().get("stale_after_hours", 36) or 36):
        problems.append(f"latest backup is {stale_hours:.0f} hours old")

    return {"ok": not problems, "problems": problems, "databases_checked": checked,
            "free_gb": round(free_gb, 1), "latest_backup": latest,
            "backup_count": len(snapshots)}
