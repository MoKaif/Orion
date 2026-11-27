"""Build identity complements release versions; working trees can contain unversioned commits."""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def identity() -> dict:
    root = Path(__file__).resolve().parents[2]
    result = {"commit": None, "dirty_at_start": None}
    try:
        command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root)]
        sha = subprocess.run([*command, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3)
        status = subprocess.run([*command, "status", "--porcelain"], capture_output=True, text=True, timeout=3)
        if sha.returncode == 0:
            result["commit"] = sha.stdout.strip()
        if status.returncode == 0:
            result["dirty_at_start"] = bool(status.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result
