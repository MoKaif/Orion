"""Layered JSON configuration.

Reads config/*.json live (no cache-staleness surprises during development). Secrets are NEVER
read from tracked config: cloud API keys come from environment variables (optionally seeded
from a gitignored config/secrets.json or .env). This is a deliberate fix for the MVP, which
committed a live key.

Two layers per section: the tracked ``<name>.json`` holds the defaults that ship, and an
optional gitignored ``<name>.local.json`` holds this machine's tuning, deep-merged on top.
Anything the user retunes from the UI goes to the local layer (``update_local``), so a knob
turned at 2am never shows up as a diff in the release commit.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("orion.config")

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _ROOT / "config"

_SECTIONS = ("settings", "models", "memory", "tools", "ui")


class Config:
    """Thin accessor over the JSON files in ``config/``."""

    def __init__(self, config_dir: Path = _CONFIG_DIR):
        self._dir = config_dir
        self._load_secrets()

    # -- secrets -----------------------------------------------------------
    def _load_secrets(self) -> None:
        """Seed os.environ from config/secrets.json if present (gitignored)."""
        secrets = self._dir / "secrets.json"
        if secrets.exists():
            try:
                for key, value in json.loads(secrets.read_text()).items():
                    os.environ.setdefault(key, str(value))
            except (json.JSONDecodeError, OSError):
                pass

    # -- reads -------------------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        """The shipped section with this machine's ``<name>.local.json`` merged over it."""
        data = _merge(self._read(name), self._read(f"{name}.local"))
        return {k: v for k, v in data.items() if not k.startswith("//")}

    def _read(self, name: str) -> dict[str, Any]:
        path = self._dir / f"{name}.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("ignoring unreadable config %s: %s", path.name, e)
            return {}

    def models(self) -> dict[str, Any]:
        return self.section("models")

    def provider_cfg(self, name: str) -> dict[str, Any]:
        return self.models().get("providers", {}).get(name, {})

    def api_key(self, provider: str) -> str | None:
        """Resolve a provider's API key from the environment, never from tracked config."""
        env_var = self.provider_cfg(provider).get("api_key_env")
        return os.environ.get(env_var) if env_var else None

    def root(self) -> Path:
        return _ROOT

    # -- clock -------------------------------------------------------------
    def apply_timezone(self) -> str | None:
        """Run the process on the user's own clock, from ``identity.timezone``.

        Cron schedules and quiet hours are wall-clock ideas — "the small hours", "07:30" — and
        the scheduler compares them against a naive ``datetime.now()``. Bare metal that reads
        as the user's local time, but a container is UTC unless told otherwise, which silently
        moved a 07:30 briefing to 13:00 IST and slid quiet hours over the very slot it was
        meant to use. Nothing had ever read ``identity.timezone``; this makes it authoritative.

        An explicit ``TZ`` in the environment always wins, so a deployment can still override.
        """
        if os.environ.get("TZ"):
            return os.environ["TZ"]
        tz = self.section("settings").get("identity", {}).get("timezone")
        if not tz:
            return None
        os.environ["TZ"] = str(tz)
        try:
            time.tzset()                      # Unix only; the target host and image are Linux
        except AttributeError:
            log.warning("cannot set process timezone on this platform; jobs run on local time")
            return None
        return str(tz)

    # -- writes ------------------------------------------------------------
    def update(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        path = self._dir / f"{name}.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        current.update(patch)
        path.write_text(json.dumps(current, indent=2))
        return current

    def update_local(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge ``patch`` into the gitignored ``<name>.local.json`` overlay layer."""
        path = self._dir / f"{name}.local.json"
        merged = _merge(self._read(f"{name}.local"), patch)
        path.write_text(json.dumps(merged, indent=2) + "\n")
        return merged


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; ``over`` wins. Lists and scalars replace rather than combine."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


config = Config()
