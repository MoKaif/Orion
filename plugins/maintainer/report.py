"""What Herald says about Maintainer, without either plugin knowing the other exists.

Registered through ``plugin_sdk.add_report_source``; Herald folds whatever is registered into
the morning briefing, the weekly letter and its alert watch. Maintainer's news is the one thing
in Orion that is genuinely waiting on the user outside the app — a pull request sitting on
GitHub is invisible from 127.0.0.1:8000, which is exactly the gap Herald exists to close.

Facts are computed here and handed to the prose model as numbers, never as blanks to fill.
"""
from __future__ import annotations

from typing import Any

from . import store

# The two accents Herald's renderer understands, repeated rather than imported: a plugin may
# not import another plugin, and two hex strings are a cheaper price than that coupling.
_GOOD = "#85bb9c"
_ATTENTION = "#d6b360"


def _window(scope: str) -> float:
    return 24 * 7 if scope == "weekly" else 18


def facts(scope: str) -> dict[str, Any]:
    c = store.conn()
    try:
        runs = store.runs(c, limit=50, since_hours=_window(scope))
        prs = store.open_prs(c)
        waiting = store.proposed(c)
        return {
            "maintainer": {
                "pull_requests_awaiting_review": len(prs),
                "pull_requests": [{"repo": p["repo"], "title": p["title"], "url": p["pr_url"]}
                                  for p in prs[:8]],
                "runs": len(runs),
                "runs_failed": len([r for r in runs if r["status"] == "failed"]),
                "verification_failed": len([r for r in runs if r["verify"] == "failed"]),
                "briefs_awaiting_your_approval": len(waiting),
                "codex_minutes": round(sum(r["duration_s"] for r in runs) / 60, 1),
                "codex_cost_usd": round(sum(r["cost_usd"] for r in runs), 2),
            }
        }
    finally:
        c.close()


def sections(scope: str) -> list[dict[str, Any]]:
    c = store.conn()
    try:
        runs = store.runs(c, limit=50, since_hours=_window(scope))
        prs = store.open_prs(c)
        waiting = store.proposed(c)
    finally:
        c.close()

    if not runs and not prs and not waiting:
        return []                                  # a silent agent gets no section

    out: list[dict[str, Any]] = []
    if prs:
        out.append({
            "heading": "Pull requests waiting on you",
            "rows": [(f"{p['repo']} · {p['title'][:60]}", _verdict(p)) for p in prs[:8]],
            "accent": _ATTENTION,
            "note": "Review and merge on GitHub. Orion cannot merge — that stays your hand.",
        })

    if runs:
        failed = [r for r in runs if r["status"] == "failed"]
        changed = sum(r["files_changed"] for r in runs)
        minutes = sum(r["duration_s"] for r in runs) / 60
        rows = [("runs", len(runs)), ("files changed", changed),
                ("Codex time", f"{minutes:.0f} min")]
        if failed:
            rows.append(("failed", len(failed)))
        out.append({
            "heading": "Maintainer" if scope == "briefing" else "Maintainer's week",
            "rows": rows,
            "accent": _ATTENTION if failed else _GOOD,
            "note": (f"{failed[0]['repo']}: {(failed[0]['error'] or 'run failed')[:120]}"
                     if failed else None),
        })

    if waiting:
        out.append({
            "heading": "Work Maintainer wants to start",
            "bullets": [f"{t['repo']}: {t['title']}" for t in waiting[:6]],
            "note": "Nothing runs until you approve it in Orion's inbox.",
        })
    return [s for s in out if s]


def _verdict(pr: dict[str, Any]) -> str:
    """One word on whether the branch stands up, so the row is worth reading on a phone."""
    if pr["verify"] == "failed":
        return "build failed"
    if pr["verify"] == "passed":
        return f"+{pr['insertions']}/-{pr['deletions']}"
    return f"{pr['files_changed']} files"


def alerts() -> list[dict[str, Any]]:
    """One mail per repo whose latest run failed, re-armed when that repo goes green again.

    Keyed on the identity of the problem (the repo), not the moment it was noticed — so a repo
    failing three nights running is one alert, and the ledger clears itself when it recovers.
    """
    c = store.conn()
    try:
        recent = store.runs(c, limit=40, since_hours=48)
    finally:
        c.close()

    latest: dict[str, dict[str, Any]] = {}
    for run in recent:                              # runs come back newest first
        latest.setdefault(run["repo"], run)

    out = []
    for repo, run in latest.items():
        key = f"maintainer_run_failed:{repo}"
        if run["status"] == "failed":
            out.append({"key": key, "heading": f"Maintainer's run on {repo} failed",
                        "detail": (run["error"] or "no reason recorded")[:300],
                        "when": run.get("ended_at") or run.get("started_at") or ""})
        else:
            out.append({"key": key, "resolved": True})
    return out
