#!/usr/bin/env python3
"""Authorize Orion's local Chronos plugin against the user's Google Calendar."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.chronos.calendar import authorize  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0,
                        help="Fixed loopback callback port for SSH forwarding (e.g. 8766)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    token = authorize(port=args.port)
    print(f"Chronos authorized. Token saved securely at {token.relative_to(ROOT)}")
