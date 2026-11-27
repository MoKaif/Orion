"""Maintainer: native engineering intelligence plus preserved legacy execution history.

Current behavior: read-only local/remote collectors and persistent project configuration.
Legacy execution is closed pending revision-bound approvals and isolated verification.
The description below records the v1 topology; it is not the native service's security model.

The other three agents work inside the house: the Conductor keeps the world model in order, the
Curator edits the vault, Herald carries news out to your inbox. Maintainer touches the projects
themselves — FinStrive, ArcVe, noxctl, Perseus, purple-buccaneers, and Orion itself.

It cannot do that with Orion's own brain, and does not try. A 3B model on a CPU box can classify
a note; it cannot land a feature. So the work is split by what each tier is actually good at:

    DeepSeek (cheap)   reads each repo overnight and proposes work worth doing
    you                approve the brief, in the inbox, before a single expensive turn runs
    Codex (host) does the engineering, in a throwaway worktree, on the host where it lives
    you                review the pull request on GitHub

**Maintainer never merges, and never writes to a checkout you use.** Every change it makes
arrives as a branch and a PR. That is the whole safety argument, and it is why this can be left
running at one in the morning: the worst case is a branch you close unread.

This module is the part that lives inside Orion — the queue, the gate, the API. The hands are
``scripts/maintainer_runner.py`` on the host, which claims approved work over the protocol
below. Orion never executes anything itself; the container has no codex, no gh, and a
read-only view of your code.
"""
from __future__ import annotations

import json
import hmac
import os
import sqlite3
import time
from html import escape
from typing import Any
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, Field

from orion.core import plugin_sdk as orion

router = APIRouter()

#: Set in gitignored config/secrets.json. No token ⇒ the runner protocol is closed and
#: Maintainer can propose work but nothing can ever claim it. That is the safe default.
_TOKEN_ENV = "MAINTAINER_RUNNER_TOKEN"


def register() -> None:
    from . import report, scan

    orion.add_agent(
        "maintainer", "Maintainer",
        tagline="Engineering",
        blurb="Tracks project configuration, Git synchronization, versions and development "
              "toolchains. Open Maintainer in the sidebar for the project workspace. "
              "Automatic development is paused while revision-bound approvals are built.",
        icon="git-pull-request", accent="idea", plugin="maintainer", order=40,
        summary=_summary, detail=_detail)

    orion.add_job("scan_repos", "0 1 * * *", scan.scan_repos, agent="maintainer",
                  label="Audit the projects",
                  description="Audits every enabled repo under a rotating maintenance focus, "
                              "even when no commits changed. Nothing runs until you approve it.",
                  limit_default=6)
    from . import intelligence
    orion.add_job("maintainer_observe", "*/10 * * * *", intelligence.refresh, agent="maintainer",
                  label="Inspect project health",
                  description="Read local Git, version and environment facts without changing checkouts.")
    orion.add_job("maintainer_remotes", "23 * * * *", intelligence.refresh_remotes,
                  agent="maintainer", label="Verify remote branch tips",
                  description="Read origin branch tips without fetching into your checkouts.")
    orion.add_job("maintainer_sweep", "*/15 * * * *", scan.sweep, agent="maintainer",
                  label="Check on the runner",
                  description="Marks a run failed if the host runner stops reporting, so a "
                              "killed process cannot leave work stuck in flight forever.")

    orion.add_widget("maintainer_prs", "Maintainer", _render_widget, plugin="maintainer")
    orion.add_inbox_source("maintainer", _inbox_items, plugin="maintainer")
    orion.add_report_source("maintainer", sections=report.sections, facts=report.facts,
                            alerts=report.alerts, plugin="maintainer")


# -- what mission control shows for this agent -----------------------------
def _summary() -> dict:
    from . import store

    c = store.conn()
    try:
        counts = store.counts(c)
        recent = store.runs(c, limit=20, since_hours=24 * 7)
    finally:
        c.close()
    return {
        "pending": counts.get("proposed", 0),
        "metrics": [
            {"label": "PRs open", "value": counts.get("open_prs", 0)},
            {"label": "briefs waiting", "value": counts.get("proposed", 0)},
            {"label": "runs this week", "value": len(recent)},
            {"label": "status", "value": status()["state"]},
        ],
    }


def _detail() -> dict:
    """The agent's page: the queue, the pull requests, the run log, and whether it can work."""
    from . import repos, store

    c = store.conn()
    try:
        return {
            "maintainer": status(),
            "queue": store.tasks(c, limit=40),
            "prs": store.open_prs(c),
            "runs": store.runs(c, limit=25),
            "repos": [{"name": r["name"], "base": r["base"], "blurb": r.get("blurb", ""),
                       "verify": r.get("verify", ""), "enabled": r.get("enabled", True)}
                      for r in repos.all_repos(enabled_only=False)],
        }
    finally:
        c.close()


#: (checked_at, result) — the agent cards poll every few seconds and this answer costs a
#: subprocess. Whether git can read a repo is not a per-second question.
_git_probe: tuple[float, bool] = (0.0, False)


def _git_reachable(configured: list[str]) -> bool:
    global _git_probe
    from . import repos

    checked_at, result = _git_probe
    if not configured:
        return False
    if time.monotonic() - checked_at < 60:
        return result
    result = bool(repos.head_sha(repos.all_repos()[0]))
    _git_probe = (time.monotonic(), result)
    return result


def status() -> dict[str, Any]:
    """Can Maintainer actually work right now — and if not, which link is broken?

    Four things have to be true: a runner token exists, the workspace is readable, git can read
    a repo at its base ref, and the host runner has spoken to us recently. Each failure names
    itself rather than presenting as "nothing happened".
    """
    from . import repos, store

    c = store.conn()
    try:
        seen = store.meta_get(c, "runner_seen")
        counts = store.counts(c)
    finally:
        c.close()

    configured = [r["name"] for r in repos.all_repos()]
    readable = repos.workspace().exists()
    git_ok = _git_reachable(configured)

    state, reason = "monitoring", ("Automatic development is paused pending revision-bound "
                                   "approvals and isolated verification. Project monitoring is available.")
    if not readable:
        state, reason = "no workspace", "The configured development workspace is unavailable."
    elif not git_ok:
        state, reason = "no git", "Legacy scan references are unavailable; inspect projects for local status."
    return {"state": state, "reason": reason, "ok": readable and git_ok,
            "execution_enabled": False,
            "runner_seen": (seen or {}).get("at"), "runner": (seen or {}).get("value"),
            "repos": configured, "workspace": str(repos.workspace()),
            "open_prs": counts.get("open_prs", 0), "waiting": counts.get("proposed", 0)}


# -- Maintainer's share of the inbox ---------------------------------------
def _inbox_items() -> list[dict[str, Any]]:
    """Briefs waiting for your go-ahead. The whole brief is on the card: you approve what you
    can read, and the effect line says plainly what your click will set in motion."""
    from . import store

    c = store.conn()
    try:
        waiting = store.proposed(c)
    finally:
        c.close()

    items = []
    for t in waiting:
        files = ", ".join(json.loads(t["files"] or "[]")[:5])
        body = t["brief"]
        if t["acceptance"]:
            body += f"\n\nDone when: {t['acceptance']}"
        if files:
            body += f"\n\nLikely files: {files}"
        items.append({
            "origin": "maintainer", "id": t["id"],
            "title": f"Work on “{t['title']}” in {t['repo']}",
            "body": body[:1600],
            "effect": "Legacy proposal preserved for review. Automatic execution is paused "
                      "until evidence-backed, revision-bound approvals are available.",
            "created_at": t.get("created_at") or "",
            "prov_agent": "Maintainer",
            "prov_label": t["rationale"] or f"proposed for {t['repo']}",
            "action_url": f"/plugins/maintainer/tasks/{t['id']}",
            "actions": [
                orion.inbox_action("Not now", "reject", "reject"),
            ],
        })
    return items


# -- the user's API (mounted at /plugins/maintainer) -----------------------
class TaskAction(BaseModel):
    action: str          # approve | reject


@router.get("/status")
async def maintainer_status():
    """The honest answer when nothing is happening."""
    return status()


@router.get("/tasks")
async def list_tasks(status: str | None = None, limit: int = 50):
    from . import store
    c = store.conn()
    try:
        return store.tasks(c, status, limit)
    finally:
        c.close()


@router.get("/tasks/{tid}")
async def read_task(tid: int):
    from . import store
    c = store.conn()
    try:
        task = store.get_task(c, tid)
        if task is None:
            return {"error": f"no task {tid}"}
        return {**task, "runs": [r for r in store.runs(c, limit=10) if r["task_id"] == tid]}
    finally:
        c.close()


@router.post("/tasks/{tid}")
async def resolve_task(tid: int, body: TaskAction):
    """The button on the inbox card. Approving only queues work — the runner picks it up."""
    from . import store
    c = store.conn()
    try:
        task = store.get_task(c, tid)
        if task is None:
            return {"ok": False, "reason": f"no task {tid}"}
        if task["status"] != "proposed":
            return {"ok": False, "reason": f"this task is already {task['status']}"}
        if body.action in ("approve", "accept", "apply"):
            raise HTTPException(409, "Execution is paused until revision-bound approvals and "
                                "isolated verification are available. The proposal is preserved.")
        if body.action not in ("reject", "dismiss"):
            raise HTTPException(422, "Expected approve or reject")
        store.set_status(c, tid, "rejected", finished_at=store.now())
        return {"ok": True, "status": "rejected", "message": "Dropped. It will not come back."}
    finally:
        c.close()


@router.get("/runs")
async def list_runs(limit: int = 25, status: str | None = None):
    from . import store
    c = store.conn()
    try:
        return store.runs(c, limit, status)
    finally:
        c.close()


@router.get("/runs/{run_id}")
async def read_run(run_id: int):
    """One run with its progress feed — what Codex actually did, step by step."""
    from . import store
    c = store.conn()
    try:
        run = store.get_run(c, run_id)
        if run is None:
            return {"error": f"no run {run_id}"}
        task = store.get_task(c, run["task_id"])
        return {**run, "task": task, "events": store.events(c, run_id)}
    finally:
        c.close()


@router.post("/scan")
async def scan_now():
    """Read the projects now instead of waiting for 01:00. Proposes; never runs anything."""
    from . import scan
    return await scan.scan_repos()


@router.get("/repos")
async def list_repos():
    from . import repos
    return repos.all_repos(enabled_only=False)


@router.get("/projects")
async def list_projects():
    from . import intelligence
    return intelligence.summary()


class RefreshRequest(BaseModel):
    discover: bool = False
    remote: bool = False


@router.post("/projects/refresh", status_code=202)
async def refresh_projects(body: RefreshRequest, background_tasks: BackgroundTasks):
    from . import intelligence
    background_tasks.add_task(intelligence.refresh, discovery=body.discover, remote=body.remote)
    return {"accepted": True, "message": "Inspection requested; results appear as projects are observed"}


class ReleasePolicy(BaseModel):
    model_config = {"extra": "forbid"}
    versioning: Literal["semantic", "calendar", "unversioned"] = "semantic"
    version_path: str = Field(default="package.json", max_length=256)
    tag_prefix: str = Field(default="v", max_length=32)
    release_branch: str = Field(default="main", min_length=1, max_length=200)


class ProjectSettings(BaseModel):
    model_config = {"extra": "forbid"}
    description: str | None = Field(default=None, max_length=1000)
    release: ReleasePolicy | None = None


class ProjectUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    revision: int = Field(ge=1)
    status: Literal["active", "paused", "archived"] | None = None
    path: str | None = Field(default=None, max_length=1000)
    settings: ProjectSettings | None = None


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate):
    from . import registry
    c = registry.conn()
    try:
        changes = body.model_dump(exclude_none=True, exclude={"revision"})
        return registry.update(c, project_id, body.revision, changes)
    except KeyError:
        raise HTTPException(404, "Project not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That checkout is already registered")
    finally:
        c.close()


# -- the runner protocol ---------------------------------------------------
# Everything below is spoken only by scripts/maintainer_runner.py on the host. It is guarded by
# a shared token because these endpoints hand out work that will run a coding agent against
# your source: an unauthenticated /claim is a remote code execution primitive, not an API.
class ClaimRequest(BaseModel):
    runner: str = "unknown"


class EventBatch(BaseModel):
    events: list[dict[str, Any]] = []


class RunResult(BaseModel):
    ok: bool = False
    branch: str | None = None
    pr_url: str | None = None
    pr_state: str | None = None
    commit_sha: str | None = None
    files_changed: int | None = None
    insertions: int | None = None
    deletions: int | None = None
    turns: int | None = None
    cost_usd: float | None = None
    duration_s: float | None = None
    verify: str | None = None
    verify_tail: str | None = None
    summary: str | None = None
    error: str | None = None


class PrStates(BaseModel):
    states: dict[str, str] = {}


def _authorize(authorization: str | None) -> None:
    expected = os.environ.get(_TOKEN_ENV)
    if not expected:
        raise HTTPException(503, f"no {_TOKEN_ENV} configured; the runner protocol is closed")
    if not hmac.compare_digest(authorization or "", f"Bearer {expected}"):
        raise HTTPException(401, "bad runner token")


def _seen(runner: str) -> None:
    from . import store
    c = store.conn()
    try:
        store.meta_set(c, "runner_seen", runner)
    finally:
        c.close()


@router.post("/runner/claim")
async def claim(body: ClaimRequest, authorization: str | None = Header(None)):
    """Hand the oldest approved task to the runner, with everything it needs to do the job.

    The repo's configuration travels with the task, so the host runner holds no copy of
    ``config/maintainer.json`` that could drift out of step with Orion's.
    """
    _authorize(authorization)
    _seen(body.runner)

    # The legacy protocol carries neither a revision-bound approval nor scoped permissions.
    # Old approvals remain in the database for review; never reinterpret them as v2 grants.
    return {"task": None, "paused": True,
            "reason": "Legacy execution disabled; awaiting revision-bound v2 approvals"}

@router.post("/runner/runs/{run_id}/events")
async def post_events(run_id: int, body: EventBatch, authorization: str | None = Header(None)):
    """Progress, and the heartbeat that proves the runner is still alive."""
    _authorize(authorization)
    from . import store
    c = store.conn()
    try:
        store.heartbeat(c, run_id)
        return {"ok": True, "stored": store.add_events(c, run_id, body.events[:200])}
    finally:
        c.close()


@router.post("/runner/runs/{run_id}/result")
async def post_result(run_id: int, body: RunResult, authorization: str | None = Header(None)):
    """The end of a run: the branch, the PR, the diffstat, the verdict, the cost."""
    _authorize(authorization)
    from . import store
    c = store.conn()
    try:
        run = store.get_run(c, run_id)
        if run is None:
            return {"ok": False, "reason": f"no run {run_id}"}
        fields = body.model_dump()
        fields.pop("ok", None)
        ok = store.successful_result(body.ok, fields)
        store.finish_run(c, run_id, ok, fields)
        store.set_status(c, run["task_id"], "done" if ok else "failed",
                         finished_at=store.now())
        return {"ok": True}
    finally:
        c.close()


@router.post("/runner/prs")
async def refresh_prs(body: PrStates, authorization: str | None = Header(None)):
    """The runner tells us which pull requests have been merged or closed, so the count of
    'waiting on you' is the truth and not a tally of everything ever opened."""
    _authorize(authorization)
    from . import store
    c = store.conn()
    try:
        for url, state in body.states.items():
            store.set_pr_state(c, url, state)
        return {"ok": True, "updated": len(body.states)}
    finally:
        c.close()


# -- dashboard widget ------------------------------------------------------
def _render_widget() -> str:
    from . import store

    st = status()
    if not st["ok"] and st["state"] != "waiting":
        return (f'<p class="empty">Maintainer is not working yet — {escape(st["reason"])}</p>')

    c = store.conn()
    try:
        prs = store.open_prs(c)
        waiting = store.proposed(c)
        recent = store.runs(c, limit=3)
    finally:
        c.close()

    if not prs and not waiting and not recent:
        return ('<p class="empty">Nothing proposed yet. Maintainer reads your projects at '
                '01:00 and will bring you work worth doing.</p>')

    rows = "".join(
        f'<li class="ws"><span class="ws-name">{escape(p["repo"])} · {escape(p["title"][:48])}'
        f'</span><span class="ws-facts">review</span></li>' for p in prs[:3])
    head = ""
    if waiting:
        head = (f'<li class="ws"><span class="ws-name">{len(waiting)} brief'
                f'{"s" if len(waiting) != 1 else ""} waiting on you</span>'
                f'<span class="ws-facts">approval</span></li>')
    elif recent:
        r = recent[0]
        head = (f'<li class="ws"><span class="ws-name">{escape(r["repo"])} · '
                f'{escape(r["title"][:44])}</span>'
                f'<span class="ws-facts">{escape(r["status"])}</span></li>')
    more = '<a class="btn link-more" href="/agents/maintainer">the work queue →</a>'
    return f'<ul class="ws-list">{head}{rows}</ul>{more}'
