"""The Executive Orchestrator — the immutable reasoning pipeline.

    Receive -> Understand intent -> Consult World Model -> Identify gaps
    -> Choose cognitive mode -> Delegate to specialist -> (maybe) run a tool
    -> Merge -> stream Respond -> Update World Model

The LLM is never the first step; the world model is. M2 adds delegation (pick the best-fit
specialist), RAG-based tool selection (only relevant tools are considered), tool execution with
the constitution's confirm gate for irreversible actions, and cognitive-mode-driven routing.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from orion.core import cognition, dispatcher, identity, specialists
from orion.core.cognition import Mode
from orion.core.config import config
from orion.core.constitution import constitution
from orion.core.gate import gate
from orion.core.providers import router
from orion.core.providers.base import Message
from orion.core.specialists import Specialist
from orion.core.tools import registry, selection
from orion.core.tools.base import BaseTool
from orion.core.world_model import world_model
from orion.core.world_model.extract import extract_candidates

# Pending confirmations for irreversible tools (ephemeral, module-level — mirrors the MVP).
_pending: dict[int, dict[str, Any]] = {}
_counter = itertools.count(1)


@dataclass
class Context:
    message: str
    mode: Mode
    specialist: Specialist
    known: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    tool_result: str | None = None

    def stable_system(self) -> str:
        """The prefix that doesn't change across turns for a given specialist — constitution
        + specialist fragment. Sent as a cacheable block so the cloud brain reuses it (prompt
        caching is prefix-match; keeping the volatile context out of here is what lets it hit)."""
        parts = [constitution.system_preamble()]
        fragment = self.specialist.system_fragment()
        if fragment:
            parts.append(fragment)
        return "\n\n".join(parts)

    def volatile_system(self) -> str:
        """Per-turn context (recalled knowledge + tool result) — kept after the cached prefix."""
        parts = []
        if self.known:
            lines = "\n".join(f"- ({k['kind']} {k['confidence']:.0%}) {k['value']}" for k in self.known)
            parts.append("What you already know that is relevant:\n" + lines)
        else:
            parts.append(
                "Your memory surfaced nothing specific for this query. If it concerns Kaif's "
                "notes, journal, or vault, remember you continuously read them — this particular "
                "detail just isn't in your recalled memory yet. Say that and offer to look deeper "
                "or re-mine the relevant notes; never claim you cannot access his files.")
        if self.tool_result:
            parts.append("A tool was run for this turn; use its result:\n" + self.tool_result)
        return "\n\n".join(parts)

    def system_prompt(self) -> str:
        return "\n\n".join(p for p in (self.stable_system(), self.volatile_system()) if p)


def assemble(message: str) -> Context:
    """Steps 1-4: intent, consult world model, identify gaps, choose mode + specialist."""
    # Config-tunable recall size: on the local CPU model, prompt length is latency.
    limit = config.section("memory").get("recall", {}).get("limit", 10)
    known = world_model.recall(message, limit=limit)
    return Context(
        message=message,
        mode=cognition.classify(message),
        specialist=specialists.select(message),
        known=known,
        gaps=[] if known else ["no relevant prior knowledge"],
    )


async def _maybe_run_tool(ctx: Context, on_event: Callable[[dict], None] | None,
                          session_id: int) -> None:
    """RAG-select relevant tools; if one clearly matches, dispatch its args and run it.

    Irreversible tools (constitution: 'ask before irreversible actions') are not executed here —
    they register a pending confirmation and emit a 'confirm' event for the UI to approve.
    """
    selected = selection.select_tools(ctx.message, ctx.specialist)
    primary = registry.match(ctx.message)
    if primary is None or primary.name not in {t.name for t in selected}:
        return

    args = await dispatcher.extract_args(primary, ctx.message)
    if args is None:                     # no local model available, or unparseable — skip cleanly
        return

    if identity.needs_approval(primary.name) or primary.requires_confirm:
        pid = next(_counter)
        _pending[pid] = {"tool": primary, "args": args, "session_id": session_id}
        if on_event:
            on_event({"type": "confirm", "id": pid, "tool": primary.name, "args": args})
        ctx.tool_result = f"[awaiting user confirmation to run '{primary.name}' with {args}]"
        return

    result = await primary.run(args)
    if on_event:
        on_event({"type": "tool", "tool": primary.name, "ok": result.ok})
    ctx.tool_result = result.format_for_context()


async def handle_turn(message: str, session_id: int,
                      on_event: Callable[[dict], None] | None = None) -> AsyncIterator[str]:
    """Full pipeline for one conversational turn. Yields response tokens.

    The whole turn runs as foreground activity: entering the gate preempts any running
    background job so chat never waits behind maintenance on the single local pipe.
    """
    async with gate.foreground():
        ctx = assemble(message)
        if on_event:
            on_event({"type": "context", "mode": ctx.mode.value,
                      "specialist": ctx.specialist.name, "known": len(ctx.known)})

        world_model.add_message(session_id, "user", message)
        await _maybe_run_tool(ctx, on_event, session_id)

        recent = config.section("memory").get("conversation", {}).get("recent_turns", 6)
        hist = world_model.history(session_id, limit=recent * 2)
        # Stable prefix is cacheable; volatile per-turn context is a separate block that
        # never invalidates the cached prefix. Both go out-of-band as `system` to the cloud
        # brain and concatenate for local models — no model-specific request shape needed.
        messages = [Message("system", ctx.stable_system(), cacheable=True)]
        volatile = ctx.volatile_system()
        if volatile:
            messages.append(Message("system", volatile))
        messages += [Message(h["role"], h["content"]) for h in hist
                     if h["role"] in ("user", "assistant")]

        reply: list[str] = []
        async for token in router.route(ctx.mode, messages, on_event=on_event):
            reply.append(token)
            yield token

        full = "".join(reply)
        world_model.add_message(session_id, "assistant", full)

        for candidate in await extract_candidates(f"User: {message}\nOrion: {full}"):
            candidate.setdefault("source", f"session:{session_id}")
            result = world_model.ingest_candidate(candidate)
            if on_event and result.get("outcome") == "queued":
                on_event({"type": "review_queued", "review_id": result["review_id"]})


async def execute_confirmed(pid: int, approve: bool) -> dict[str, Any]:
    """Run (or cancel) a tool that was awaiting user confirmation."""
    pending = _pending.pop(pid, None)
    if pending is None:
        return {"error": "unknown or expired confirmation id"}
    if not approve:
        return {"outcome": "cancelled", "tool": pending["tool"].name}
    result = await pending["tool"].run(pending["args"])
    return {"outcome": "executed", "tool": pending["tool"].name,
            "ok": result.ok, "output": result.output, "meta": result.meta}
