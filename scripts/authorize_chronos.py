#!/usr/bin/env python3
"""Authorize Orion's local Chronos plugin against the user's Google Calendar."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.chronos.calendar import authorize  # noqa: E402


if __name__ == "__main__":
    token = authorize()
    print(f"Chronos authorized. Token saved securely at {token.relative_to(ROOT)}")
