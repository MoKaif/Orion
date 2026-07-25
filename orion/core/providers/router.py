"""Cost-aware model router — the single place cost is controlled.

  1. Map a cognitive Mode -> a provider (config/models.json "routing").
  2. Local-first with visible fallback: try the resolved provider; if it is unavailable or
     fails *before the first token*, escalate through the fallback chain and emit a
     'fallback' event so a broken primary is never silently masked. Once real output has
     streamed, never switch (no duplicated tokens).
  3. Enforce a hard daily token budget.

The foreground/background local-model gate (chat preempts background LLM calls) arrives in M3.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import AsyncIterator, Callable

from orion.core.cognition import Mode
from orion.core.config import config
from .base import BaseProvider, Message, Usage

log = logging.getLogger("orion.router")

_PROVIDERS: dict[str, BaseProvider] = {}
_USAGE = config.root() / "data" / "usage.json"


def register(name: str, provider: BaseProvider) -> None:
    _PROVIDERS[name] = provider


def get(name: str) -> BaseProvider | None:
    return _PROVIDERS.get(name)


def provider_for(mode: Mode) -> str:
    return config.models().get("routing", {}).get(mode.value, "ollama")


def _candidates(mode: Mode) -> list[str]:
    """Resolved provider first, then the configured fallback chain (deduped)."""
    primary = provider_for(mode)
    chain = config.models().get("escalation", {}).get("fallback_chain", [])
    seen, out = set(), []
    for name in [primary, *chain]:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


# -- token budget + per-model $ cost tracking -----------------------------
# usage.json shape (per day): {"tokens": int, "cost_usd": float,
#   "by_model": {model: {input, output, cache_read, cache_write, cost_usd}}}.
# Older files stored a bare int per day; readers below tolerate that and migrate on write.
def _load() -> dict:
    try:
        return json.loads(_USAGE.read_text())
    except Exception:
        return {}


def _today_entry(data: dict) -> dict:
    from datetime import date
    entry = data.get(date.today().isoformat())
    if isinstance(entry, int):            # legacy bare-token format
        return {"tokens": entry, "cost_usd": 0.0, "by_model": {}}
    if not isinstance(entry, dict):
        return {"tokens": 0, "cost_usd": 0.0, "by_model": {}}
    entry.setdefault("tokens", 0)
    entry.setdefault("cost_usd", 0.0)
    entry.setdefault("by_model", {})
    return entry


def _price(model: str) -> dict:
    """Per-1M-token pricing row (input/output/cache_read/cache_write) from config."""
    return config.models().get("pricing", {}).get(model, {})


def _cost(u: Usage) -> float:
    p = _price(u.model)
    if not p:
        return 0.0
    return (u.input_tokens * p.get("input", 0.0)
            + u.output_tokens * p.get("output", 0.0)
            + u.cache_read_tokens * p.get("cache_read", 0.0)
            + u.cache_write_tokens * p.get("cache_write", 0.0)) / 1_000_000


def _spent_today() -> int:
    return _today_entry(_load()).get("tokens", 0)


def _record(u: Usage) -> None:
    """Accumulate today's tokens + dollar cost, split per model, for budget and telemetry."""
    from datetime import date
    _USAGE.parent.mkdir(parents=True, exist_ok=True)
    data = _load()
    entry = _today_entry(data)
    cost = _cost(u)
    entry["tokens"] += u.total_tokens
    entry["cost_usd"] = round(entry["cost_usd"] + cost, 6)
    m = entry["by_model"].setdefault(
        u.model or "unknown",
        {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0})
    m["input"] += u.input_tokens
    m["output"] += u.output_tokens
    m["cache_read"] += u.cache_read_tokens
    m["cache_write"] += u.cache_write_tokens
    m["cost_usd"] = round(m["cost_usd"] + cost, 6)
    data[date.today().isoformat()] = entry
    _USAGE.write_text(json.dumps(data))


def budget_ok() -> bool:
    ceiling = config.models().get("budget", {}).get("daily_token_ceiling", 0)
    return ceiling == 0 or _spent_today() < ceiling


def usage() -> dict:
    """Today's spend against the configured ceiling — tokens, dollars, and per-model split."""
    ceiling = config.models().get("budget", {}).get("daily_token_ceiling", 0)
    entry = _today_entry(_load())
    spent = entry.get("tokens", 0)
    return {"spent": spent, "ceiling": ceiling,
            "fraction": (spent / ceiling) if ceiling else 0.0,
            "cost_usd": entry.get("cost_usd", 0.0),
            "by_model": entry.get("by_model", {})}


# -- routing --------------------------------------------------------------
async def route(mode: Mode, messages: list[Message],
                on_event: Callable[[dict], None] | None = None) -> AsyncIterator[str]:
    """Stream a response for `mode`, escalating through providers on pre-content failure."""
    if not budget_ok():
        yield "[budget] daily token ceiling reached — cloud calls paused. Try a local model."
        return

    candidates = _candidates(mode)
    for i, name in enumerate(candidates):
        provider = _PROVIDERS.get(name)
        if provider is None or not await provider.is_available():
            continue
        started = False
        text_len = 0
        try:
            async for token in provider.stream(messages, mode=mode):
                if not started and i > 0 and on_event:
                    on_event({"type": "fallback", "answered_by": name,
                              "reason": "primary unavailable"})
                started = True
                text_len += len(token)
                yield token
            if started:
                # Prefer the provider's real accounting (Claude reports exact input/output
                # + cache tokens per model); fall back to a char/4 estimate for locals,
                # tagged with the local model so it still costs against the right price row.
                real = getattr(provider, "pop_last_usage", lambda: None)()
                if real is None:
                    real = Usage(
                        input_tokens=sum(len(m.content) for m in messages) // 4,
                        output_tokens=text_len // 4,
                        model=config.provider_cfg(name).get("model", name))
                _record(real)
                return
        except Exception as e:
            if started:
                log.warning("provider %s failed mid-stream; not switching: %s", name, e)
                return
            # warning, not info: default logging hides info, and a silently-failing
            # provider chain is exactly what must never be invisible.
            log.warning("provider %s failed pre-content; escalating: %r", name, e)
            continue

    yield "[no provider available] Start Ollama locally, or add a cloud API key in config."
