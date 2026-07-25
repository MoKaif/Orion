"""Background lifecycle — Orion's 'own life' when the user is away.

Idle-by-default (odysseus lesson: no warmups/keepalives competing with real use). Jobs are
cron-scheduled and run through the foreground/background gate, so interactive chat always wins
the single local-model pipe. Degrades gracefully: if croniter isn't installed, time-scheduling
is disabled but jobs can still be triggered on demand (POST /jobs/{name}/run).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from orion.core import agents
from orion.core.config import config
from orion.core.gate import gate

log = logging.getLogger("orion.scheduler")

Job = Callable[[], Awaitable[object]]

_RUNS_FILE = config.root() / "data" / "job_runs.json"
_HISTORY_KEEP = 20   # runs kept per job in the persisted log


def _job_cfg(name: str) -> dict:
    return config.section("jobs").get("jobs", {}).get(name, {})


def configured_cron(name: str, default: str) -> str:
    """Let the user retune a job's schedule in config/jobs.json without touching code."""
    return _job_cfg(name).get("cron", default)


def job_limit(name: str, default: int) -> int:
    """How much work one run of this job should do (notes per pass, etc.), user-tunable.

    Read at call time rather than registration time, so changing the batch size in the UI
    takes effect on the very next run without a restart.
    """
    try:
        return max(1, int(_job_cfg(name).get("limit", default)))
    except (TypeError, ValueError):
        return default


@dataclass
class ScheduledJob:
    name: str
    cron: str
    run: Job
    foreground_preemptible: bool = True
    agent: str = agents.DEFAULT_AGENT     # which agent's page this job appears on
    label: str = ""                       # human name ("Grammar & spelling")
    description: str = ""                 # one line: what a run actually does
    limit_default: int | None = None      # exposes a tunable batch size when set
    last_run: str | None = None
    last_result: object = None
    running_since: str | None = None
    queued_since: str | None = None       # waiting for the gate (chat has the local pipe)
    _next: datetime | None = field(default=None, repr=False)

    @property
    def next_run(self) -> str | None:
        return self._next.isoformat(timespec="seconds") if self._next else None

    @property
    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ")

    @property
    def limit(self) -> int | None:
        """The batch size in force right now, or None for jobs that don't take one."""
        return None if self.limit_default is None else job_limit(self.name, self.limit_default)


class Scheduler:
    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}
        self._task: asyncio.Task | None = None
        self._requested: set[asyncio.Task] = set()   # hand-started runs, kept from GC
        self._croniter = None
        self._history: dict[str, list[dict]] | None = None   # lazy-loaded from disk

    def register(self, job: ScheduledJob) -> None:
        self._jobs[job.name] = job

    def jobs(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    # -- run history (persisted so the Agents UI survives restarts) --------
    def history(self, name: str) -> list[dict]:
        if self._history is None:
            try:
                self._history = json.loads(_RUNS_FILE.read_text())
            except Exception:
                self._history = {}
        return self._history.get(name, [])

    def _record_run(self, name: str, ok: bool, seconds: float, result: object) -> None:
        self.history(name)                       # ensure loaded
        runs = self._history.setdefault(name, [])
        runs.insert(0, {
            "at": datetime.now().isoformat(timespec="seconds"),
            "ok": ok,
            "seconds": round(seconds, 1),
            "result": str(result)[:400],
        })
        del runs[_HISTORY_KEEP:]
        try:
            _RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _RUNS_FILE.write_text(json.dumps(self._history))
        except OSError as e:
            log.warning("could not persist job runs: %s", e)

    async def _execute(self, job: ScheduledJob) -> object:
        """Run a job once: track running state, time it, log the run, never raise."""
        job.queued_since = None
        job.running_since = datetime.now().isoformat(timespec="seconds")
        start = time.monotonic()
        try:
            result = await job.run()
            ok = True
        except Exception as e:
            log.warning("job %s failed: %s", job.name, e)
            result, ok = {"error": str(e)}, False
        finally:
            job.running_since = None
        job.last_result = result
        job.last_run = datetime.now().isoformat(timespec="seconds")
        self._record_run(job.name, ok, time.monotonic() - start, result)
        return result

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        cfg = config.section("jobs")
        if not cfg.get("enabled", True):
            log.info("scheduler disabled by config")
            return
        try:
            from croniter import croniter
            self._croniter = croniter
        except Exception as e:
            log.warning("croniter unavailable (%s); time-scheduling off, manual run only", e)
            return
        now = datetime.now()
        for job in self._jobs.values():
            if self._job_enabled(job.name):
                job._next = self._croniter(job.cron, now).get_next(datetime)
        self._task = asyncio.create_task(self._loop())
        log.info("scheduler started with %d job(s)", len(self._jobs))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # -- loop --------------------------------------------------------------
    async def _loop(self) -> None:
        tick = config.section("jobs").get("tick_seconds", 60)
        try:
            while True:
                await self._run_due(datetime.now())
                await asyncio.sleep(tick)
        except asyncio.CancelledError:
            raise

    async def _run_due(self, now: datetime) -> list[str]:
        fired = []
        for job in self._jobs.values():
            if job._next is not None and now >= job._next:
                fired.append(job.name)
                await self._dispatch(job)
                job._next = self._croniter(job.cron, now).get_next(datetime)
        return fired

    async def _dispatch(self, job: ScheduledJob) -> None:
        # background work yields to (and is preempted by) interactive chat.
        await gate.run_background(self._execute(job), preemptible=job.foreground_preemptible)

    async def run_now(self, name: str) -> dict:
        """Trigger a job off-schedule and wait for it (the scripting/CLI path).

        A hand-started run still goes through the gate so it can't fight an in-flight chat turn
        for the local model — but unlike a cron run it is **not** preemptible: the user asked
        for it, so it waits its turn instead of being cancelled.
        """
        job = self._jobs.get(name)
        if job is None:
            return {"error": f"unknown job '{name}'"}
        if job.running_since or job.queued_since:
            return {"error": f"'{name}' is already running (since "
                             f"{job.running_since or job.queued_since})"}
        job.queued_since = datetime.now().isoformat(timespec="seconds")
        try:
            await gate.run_background(self._execute(job), preemptible=False)
        finally:
            job.queued_since = None
        return {"ran": name, "result": job.last_result, "at": job.last_run}

    def request_run(self, name: str) -> dict:
        """Start a job in the background and return at once (the UI path).

        The job is marked queued straight away so the page can show 'queued' while it waits
        for the gate, then 'running' once it has the pipe.
        """
        job = self._jobs.get(name)
        if job is None:
            return {"error": f"unknown job '{name}'"}
        if job.running_since or job.queued_since:
            return {"queued": False,
                    "reason": f"already {'running' if job.running_since else 'queued'}"}
        job.queued_since = datetime.now().isoformat(timespec="seconds")
        task = asyncio.create_task(self._run_requested(job))
        self._requested.add(task)
        task.add_done_callback(self._requested.discard)
        return {"queued": True, "job": name}

    async def _run_requested(self, job: ScheduledJob) -> None:
        try:
            await gate.run_background(self._execute(job), preemptible=False)
        finally:
            job.queued_since = None

    def apply_config(self) -> list[str]:
        """Re-read jobs.json (+ local overlay) after a config edit and reschedule.

        Returns the names whose next fire time changed. A disabled job loses its next run
        entirely; re-enabling it schedules from now.
        """
        changed = []
        now = datetime.now()
        for job in self._jobs.values():
            cron = configured_cron(job.name, job.cron)
            before = job._next
            job.cron = cron
            if not self._job_enabled(job.name) or self._croniter is None:
                job._next = None
            else:
                try:
                    job._next = self._croniter(cron, now).get_next(datetime)
                except Exception as e:
                    log.warning("job %s has an unusable cron %r: %s", job.name, cron, e)
                    job._next = None
            if job._next != before:
                changed.append(job.name)
        return changed

    def _job_enabled(self, name: str) -> bool:
        jobs = config.section("jobs").get("jobs", {})
        return jobs.get(name, {}).get("enabled", True)

    def enabled(self, name: str) -> bool:
        """Public: is this job allowed to fire on its schedule?"""
        return self._job_enabled(name)


scheduler = Scheduler()
