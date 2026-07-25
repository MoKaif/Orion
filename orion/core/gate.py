"""Foreground/background gate for the single local-model pipe.

On an 8GB CPU-only box there is one slow local model. Interactive chat must never wait behind a
background job. This gate (an odysseus pattern) lets background work run only while no foreground
turn is active, and **preempts** (cancels) any running preemptible background task the moment a
foreground turn begins. Cloud calls are unaffected — they don't compete for the local pipe.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

log = logging.getLogger("orion.gate")


class LocalModelGate:
    def __init__(self) -> None:
        self._fg = 0
        self._idle = asyncio.Event()
        self._idle.set()                      # idle until a foreground turn starts
        self._bg: set[asyncio.Task] = set()

    def foreground_active(self) -> bool:
        return self._fg > 0

    @asynccontextmanager
    async def foreground(self):
        """Wrap an interactive turn. Entering preempts running background jobs."""
        self._fg += 1
        self._idle.clear()
        for task in list(self._bg):
            task.cancel()                     # chat preempts background work
        try:
            yield
        finally:
            self._fg -= 1
            if self._fg == 0:
                self._idle.set()

    async def run_background(self, coro, *, preemptible: bool = True) -> bool:
        """Run a background coroutine, waiting for idle first. Returns False if preempted."""
        async def runner():
            await self._idle.wait()
            await coro

        task = asyncio.create_task(runner())
        if preemptible:
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
        try:
            await task
            return True
        except asyncio.CancelledError:
            log.info("background job preempted by foreground activity")
            return False


gate = LocalModelGate()
