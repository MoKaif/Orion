"""Layered JSON configuration.

Reads config/*.json live (no cache-staleness surprises during development). Secrets are NEVER
read from tracked config: cloud API keys come from environment variables (optionally seeded
from a gitignored config/secrets.json or .env). This is a deliberate fix for the MVP, which
committed a live key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
        path = self._dir / f"{name}.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        return {k: v for k, v in data.items() if not k.startswith("//")}

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

    # -- writes ------------------------------------------------------------
    def update(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        path = self._dir / f"{name}.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        current.update(patch)
        path.write_text(json.dumps(current, indent=2))
        return current


config = Config()
