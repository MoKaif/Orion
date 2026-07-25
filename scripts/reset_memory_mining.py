#!/usr/bin/env python3
"""Clear the journal-mining backlog and re-arm the Curator to re-mine with the tuned prompt.

The old ``grow_memory`` pass over-extracted daily-log trivia ("very sleepy", "difficult as
always") into the review inbox. After tightening the extraction (stricter prompt + trivia guard
+ higher confidence floor in ``plugins/curator/memory.py``), this script gives a clean slate:

  1. Rejects every *pending* journal-sourced knowledge item in the review inbox
     (``source LIKE 'vault:%'``), so the noise disappears from the inbox.
  2. Clears the Curator's ``mined_sha`` checkpoints so every note is eligible to be mined again.

It does NOT re-mine — run that yourself afterwards so it's deliberate and rate-limited:

    curl -X POST 'http://127.0.0.1:8020/plugins/curator/passes/memory?limit=20'
    # or in the UI: Agents -> Curator -> "Journal -> memory" -> Run now

Dry-run by default; pass --apply to make changes.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORION_DB = ROOT / "data" / "orion.db"
CURATOR_DB = ROOT / "data" / "curator.db"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="make changes (default is a dry run)")
    ap.add_argument("--all", action="store_true",
                    help="reject ALL pending knowledge items, not just vault-sourced ones")
    args = ap.parse_args()

    # 1. count / reject the pending journal-mined inbox items
    where = ("status='pending' AND item_type='knowledge'"
             + ("" if args.all else " AND payload LIKE '%\"source\": \"vault:%'"))
    with sqlite3.connect(ORION_DB) as c:
        n = c.execute(f"SELECT COUNT(*) FROM review_inbox WHERE {where}").fetchone()[0]
        print(f"review_inbox: {n} pending {'knowledge' if args.all else 'journal-mined'} item(s) "
              f"to reject")
        if args.apply and n:
            c.execute(f"UPDATE review_inbox SET status='rejected' WHERE {where}")

    # 2. clear the Curator memory-pass checkpoints so notes are re-mined
    if CURATOR_DB.exists():
        with sqlite3.connect(CURATOR_DB) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(notes)")}
            if "mined_sha" in cols:
                m = c.execute(
                    "SELECT COUNT(*) FROM notes WHERE mined_sha IS NOT NULL").fetchone()[0]
                print(f"curator notes: {m} note(s) will be re-armed for mining")
                if args.apply and m:
                    c.execute("UPDATE notes SET mined_sha=NULL")
    else:
        print("curator.db not found — skipping checkpoint reset")

    if args.apply:
        print("\nDone. Now re-mine deliberately:")
        print("  curl -X POST 'http://127.0.0.1:8020/plugins/curator/passes/memory?limit=20'")
    else:
        print("\nDry run — nothing changed. Re-run with --apply to commit.")


if __name__ == "__main__":
    main()
