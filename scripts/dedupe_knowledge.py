#!/usr/bin/env python3
"""Collapse duplicate knowledge rows left behind by the append-only ingest bug.

Until `add_knowledge` learned to upsert, every hourly vault index re-inserted each note's
content as a brand-new row. One note ended up with a row per hour — 94% of the table was
copies, and recall spent its handful of context slots on the same paragraph thirty times over.

This keeps the **oldest** row of each (entity, key, value) — the one whose id the vector index
already points at — deletes the rest, drops their orphaned embeddings, and VACUUMs. It also
removes entities indexed out of `.trash`/`.obsidian`, which is where the duplicate *entity*
notices came from.

    python scripts/dedupe_knowledge.py            # report only, changes nothing
    python scripts/dedupe_knowledge.py --apply    # back up, then clean

The backup is a plain file copy next to the db; restore by moving it back.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STALE_DIRS = (".trash/", ".obsidian/")


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def survey(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) c FROM knowledge").fetchone()["c"]
    distinct = conn.execute(
        "SELECT COUNT(*) c FROM (SELECT DISTINCT entity_id, key, value FROM knowledge)"
    ).fetchone()["c"]
    stale = conn.execute(
        "SELECT id, name, source FROM entities WHERE "
        + " OR ".join("lower(COALESCE(source, canonical_key, '')) LIKE ?" for _ in STALE_DIRS),
        tuple(f"%{d}%" for d in STALE_DIRS),
    ).fetchall()
    return {"total": total, "distinct": distinct, "redundant": total - distinct,
            "stale_entities": [dict(r) for r in stale]}


def dedupe(conn: sqlite3.Connection) -> list[int]:
    """Ids of the redundant rows — every row but the oldest of each (entity, key, value)."""
    rows = conn.execute(
        """SELECT id FROM knowledge WHERE id NOT IN (
               SELECT MIN(id) FROM knowledge GROUP BY entity_id, key, value)"""
    ).fetchall()
    return [r["id"] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually write (default: report only)")
    ap.add_argument("--keep-stale", action="store_true",
                    help="leave .trash/.obsidian entities in place")
    ap.add_argument("--db", default=None, help="path to orion.db (default: data/orion.db)")
    args = ap.parse_args()

    db = Path(args.db) if args.db else ROOT / "data" / "orion.db"
    if not db.exists():
        print(f"no database at {db}", file=sys.stderr)
        return 1

    conn = connect(db)
    before = survey(conn)
    redundant = dedupe(conn)
    stale = [] if args.keep_stale else before["stale_entities"]

    print(f"database        {db}")
    print(f"knowledge rows  {before['total']:,}  ({before['distinct']:,} distinct)")
    print(f"redundant       {len(redundant):,} rows "
          f"({len(redundant) / before['total']:.0%} of the table)" if before["total"] else "")
    print(f"stale entities  {len(stale)} indexed from .trash/.obsidian")
    for e in stale[:10]:
        print(f"                #{e['id']} {e['source'] or e['name']}")

    if not args.apply:
        print("\nreport only — pass --apply to make these changes")
        conn.close()
        return 0

    # sqlite's own backup API rather than a file copy: the db runs in WAL mode, where recent
    # commits can still live in the -wal sidecar and a plain copy would silently lose them.
    backup = db.with_name(f"{db.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{db.suffix}")
    with sqlite3.connect(backup) as dest:
        conn.backup(dest)
    print(f"\nbacked up to    {backup.name}")

    # entity removal first, so its knowledge doesn't get counted twice
    dropped_rows = 0
    for e in stale:
        cur = conn.execute("DELETE FROM knowledge WHERE entity_id=?", (e["id"],))
        dropped_rows += cur.rowcount or 0
        conn.execute("DELETE FROM entities WHERE id=?", (e["id"],))
    conn.commit()

    remaining = dedupe(conn)
    for chunk in (remaining[i:i + 500] for i in range(0, len(remaining), 500)):
        conn.execute(f"DELETE FROM knowledge WHERE id IN ({','.join('?' * len(chunk))})",
                     tuple(chunk))
    conn.commit()

    after = survey(conn)
    conn.close()

    # drop embeddings whose rows are gone; harmless when the vector deps aren't installed
    try:
        from orion.core.world_model import world_model
        from orion.core.world_model.vectors import vectors
        vectors.bind(world_model._path)
        gone = [f"k:{kid}" for kid in remaining] + [f"e:{e['id']}" for e in stale]
        print(f"embeddings      {vectors.remove(gone)} removed")
    except Exception as e:
        print(f"embeddings      skipped ({type(e).__name__}: {e})")

    conn = connect(db)
    conn.execute("VACUUM")
    conn.close()

    print(f"removed         {len(remaining):,} duplicate rows"
          f" + {dropped_rows:,} from stale entities")
    print(f"knowledge rows  {before['total']:,} -> {after['total']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
