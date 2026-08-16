"""The nightly audit: find worthwhile maintenance in every configured project.

This is the only place Maintainer decides anything, and it is deliberately the cheap tier.
DeepSeek reads a block of facts gathered by ``repos.digest`` — the README, the Checkpoint
state, recent commits, TODO markers, and a rotating sample of real source files — and proposes
at most a couple of concrete tasks per repo. Codex is never involved here: a coding agent is
too expensive to spend on "is there anything worth doing", and this stage produces prose.

The same rule as Herald's lede governs it: **the model shapes facts it is given, it never
supplies them.** A proposal names files that appeared in the digest or it names none. And
nothing it produces touches a repo — a proposal is a row in the inbox with your name on it.

Everything degrades. No key, no budget, provider down, unparseable JSON ⇒ the pass logs and
proposes nothing. It audits unchanged repositories too, but never invents work merely to fill
a nightly quota.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from orion.core.scheduler import job_limit

from . import repos, store

log = logging.getLogger("orion.maintainer")

_AUDIT_FOCUSES = ("correctness", "tests", "reliability", "maintainability", "documentation")

_BRIEFER = (
    "You are Maintainer, the part of Orion that proposes code changes to its user's own "
    "projects. You are given a block of true facts about one repository: its README, its "
    "checkpoint notes, recent commits, TODO markers, and selected source excerpts. The input "
    "also names tonight's audit focus and recently considered tasks.\n\n"
    "Propose at most {n} pieces of work worth doing next. Each one must be:\n"
    "  * small enough for a competent engineer to finish in one sitting,\n"
    "  * verifiable — the repo's build or tests can tell whether it worked,\n"
    "  * grounded strictly in the facts given. Never invent a file, a symbol, a bug or a "
    "dependency that does not appear above. Do not repeat or paraphrase a recently considered "
    "task. Aim to find one concrete improvement under tonight's audit focus; return an empty "
    "list only when the supplied evidence genuinely supports no worthwhile change.\n\n"
    "Prefer: finishing something the commits show as half-done, a TODO with real consequences, "
    "missing documentation a newcomer would need, a test for logic that recently changed. "
    "Avoid: broad refactors, dependency upgrades, redesigns, anything touching secrets, auth, "
    "payments or database migrations, and anything you would need to ask a question about.\n\n"
    "Reply with JSON only: {{\"tasks\": [{{\"title\": \"imperative, under 70 characters\", "
    "\"rationale\": \"one sentence to the user on why this is worth their approval\", "
    "\"brief\": \"2-5 sentences of instruction to the engineer who will do it\", "
    "\"acceptance\": \"how we will know it worked\", "
    "\"files\": [\"paths from the facts above\"], \"risk\": \"low|medium|high\"}}]}}"
)


async def _propose(digest: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Ask the cheap cloud tier for candidate tasks. Returns [] on any failure."""
    from orion.core.cognition import Mode
    from orion.core.providers import router
    from orion.core.providers.base import Message

    messages = [
        Message(role="system", content=_BRIEFER.format(n=limit), cacheable=True),
        Message(role="user", content=json.dumps(digest, indent=2, default=str)[:24000]),
    ]
    try:
        chunks = []
        async for token in router.route(Mode.REASONING, messages):
            chunks.append(token)
        raw = "".join(chunks).strip()
    except Exception as e:
        log.info("maintainer could not draft briefs for %s: %s", digest.get("repo"), e)
        return []

    # the router yields these as ordinary text when it has nothing to route to
    if not raw or raw.startswith("[budget]") or raw.startswith("[no provider"):
        log.info("maintainer scan skipped %s: %s", digest.get("repo"), raw[:60] or "empty reply")
        return []

    data = _loads(raw)
    items = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and str(i.get("title", "")).strip()][:limit]


def _loads(raw: str) -> Any:
    """Tolerate the usual wrappers around JSON without letting a bad reply become an exception."""
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = raw.find(open_c), raw.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(raw[s:e + 1])
            except json.JSONDecodeError:
                continue
    return {}


def _norm(title: str) -> str:
    return "".join(ch for ch in title.lower() if ch.isalnum() or ch == " ").strip()


async def scan_repos() -> dict[str, Any]:
    """Scheduled nightly. Audit repositories whether or not their base branch changed."""
    cfg = repos.scan_cfg()
    per_repo = int(cfg.get("max_candidates_per_repo", 2) or 2)
    budget = job_limit("scan_repos", 6)          # repos per run, retunable from the agent page

    c = store.conn()
    try:
        expired = store.expire_stale_proposals(c, int(cfg.get("expire_briefs_days", 14) or 14))
        proposed, scanned, skipped = 0, [], []

        configured = repos.all_repos()
        # When the user sets a budget below the repo count, rotate the starting point daily so
        # the same early entries cannot starve later projects forever.
        offset = date.today().toordinal() % len(configured) if configured else 0
        ordered = configured[offset:] + configured[:offset]
        for repo in ordered:
            if len(scanned) >= budget:
                break
            sha = repos.head_sha(repo)
            if not sha:
                skipped.append(f"{repo['name']} (unreadable)")
                continue
            last = store.last_scan(c, repo["name"])
            focus = _AUDIT_FOCUSES[(date.today().toordinal() + sum(map(ord, repo["name"])))
                                   % len(_AUDIT_FOCUSES)]
            prior = store.recent_titles(
                c, repo["name"], int(cfg.get("dedupe_days", 90) or 90))
            digest = repos.digest(
                repo, since_sha=last["head_sha"] if last else None,
                focus=focus, previous_tasks=prior)
            candidates = await _propose(digest, per_repo)

            seen = {_norm(t) for t in prior}
            filed = 0
            for item in candidates:
                title = str(item.get("title", "")).strip()[:120]
                if _norm(title) in seen:
                    continue                      # already in flight; never propose it twice
                seen.add(_norm(title))
                store.add_task(
                    c, repo["name"], title, str(item.get("brief", "")).strip()[:4000],
                    rationale=str(item.get("rationale", "")).strip()[:500],
                    acceptance=str(item.get("acceptance", "")).strip()[:500],
                    files=json.dumps([str(f)[:200] for f in (item.get("files") or [])][:12]),
                    risk=str(item.get("risk", "low")).lower()[:10] or "low",
                    source="scan")
                filed += 1

            store.mark_scanned(c, repo["name"], sha, filed)
            scanned.append(repo["name"])
            proposed += filed

        return {"ok": True, "scanned": scanned, "skipped": skipped,
                "proposed": proposed, "expired": expired}
    finally:
        c.close()


async def sweep() -> dict[str, Any]:
    """Every quarter hour: reap runs whose runner has gone quiet.

    A killed runner, a rebooted host or a hung build would otherwise leave a task stuck in
    ``running`` forever and its repo silently blocked. The heartbeat is what makes the
    difference between "still working" and "gone" observable at all.
    """
    # The runner stops Codex at max_run_minutes and heartbeats every minute meanwhile, so
    # silence past that plus a margin means the process itself is gone — not merely busy.
    minutes = float((repos.settings().get("runner") or {}).get("max_run_minutes", 45) or 45) + 15
    c = store.conn()
    try:
        reaped = []
        for run in store.stale_runs(c, minutes):
            store.finish_run(c, run["id"], False, {
                "error": f"the runner stopped reporting; no heartbeat for {minutes:.0f} minutes"})
            store.set_status(c, run["task_id"], "failed", finished_at=store.now())
            reaped.append(run["id"])
        return {"ok": True, "reaped": reaped}
    finally:
        c.close()
