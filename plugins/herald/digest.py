"""What Herald actually has to say — the four letters, and the facts behind them.

Herald owns no data of its own beyond the outbox. Everything in a briefing is read back out of
the systems that already know it: the world model's stats, the unified inbox (so a Curator
proposal reaches your phone without Herald knowing Curator exists), the scheduler's run history,
and the router's usage ledger.

**The figures are computed here; only the prose comes from a model.** ``_lede`` hands
deepseek-v4-flash a JSON block of already-true numbers and asks for two paragraphs of framing —
it never sees a blank to fill in, so it cannot invent a count. If the provider is down, over
budget, or simply slow, the letter goes out without its opening paragraph rather than not at all.

Every builder returns a dict describing what happened instead of raising, because these run as
scheduled jobs and a failed briefing must not read as a failed *system*.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from orion.core.config import config

from . import mailer, render, store

log = logging.getLogger("orion.herald")

_USAGE = config.root() / "data" / "usage.json"


# -- reading the rest of Orion --------------------------------------------
def _stats() -> dict[str, int]:
    from orion.core.world_model import world_model
    try:
        return world_model.stats()
    except Exception as e:
        log.warning("herald could not read world-model stats: %s", e)
        return {}


def _inbox() -> list[dict[str, Any]]:
    """Every waiting item from every source. A source that fails is already skipped upstream."""
    from orion.core import inbox
    try:
        return inbox.items()
    except Exception as e:
        log.warning("herald could not read the inbox: %s", e)
        return []


def _parse(ts: str | None) -> datetime | None:
    """Parse one of the several ISO shapes in the codebase; naive stamps are read as UTC."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _age_days(ts: str | None) -> float:
    dt = _parse(ts)
    if dt is None:
        return 0.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _runs_since(hours: float) -> list[dict[str, Any]]:
    """Job runs inside the window, newest first, tagged with the job's human label."""
    from orion.core.scheduler import scheduler
    cutoff = datetime.now() - timedelta(hours=hours)
    out = []
    for job in scheduler.jobs():
        for run in scheduler.history(job.name):
            at = _parse(run.get("at"))
            # history stamps are local naive; compare on the same footing
            if at is None or at.replace(tzinfo=None) < cutoff:
                continue
            out.append({**run, "job": job.name, "label": job.display_label,
                        "agent": job.agent})
    return sorted(out, key=lambda r: r.get("at", ""), reverse=True)


def _spend(days: int = 1) -> dict[str, Any]:
    """Cloud spend over the last N days from the router's usage ledger."""
    try:
        data = json.loads(_USAGE.read_text())
    except Exception:
        return {"cost_usd": 0.0, "tokens": 0, "by_model": {}}
    total, tokens = 0.0, 0
    models: Counter = Counter()
    for i in range(days):
        entry = data.get((date.today() - timedelta(days=i)).isoformat())
        if isinstance(entry, int):              # legacy bare-token day
            tokens += entry
            continue
        if not isinstance(entry, dict):
            continue
        total += float(entry.get("cost_usd", 0.0) or 0.0)
        tokens += int(entry.get("tokens", 0) or 0)
        for model, m in (entry.get("by_model") or {}).items():
            models[model] += float(m.get("cost_usd", 0.0) or 0.0)
    return {"cost_usd": round(total, 4), "tokens": tokens, "by_model": dict(models)}


def _by_agent(items: list[dict[str, Any]]) -> Counter:
    return Counter(i.get("prov_agent") or "Orion" for i in items)


def _run_tally(runs: list[dict[str, Any]], limit: int = 10) -> list[tuple[str, str]]:
    """Collapse runs into one row per job: ``("Vault search index", "ok ×10")``.

    Jobs fire at wildly different rates — the vault index is hourly, the Curator's passes are
    nightly — so a flat list of runs is really a list of whichever job runs most often. Anything
    that failed sorts to the top, because that is the row worth reading first.
    """
    tally: dict[str, Counter] = {}
    for r in runs:
        tally.setdefault(r["label"], Counter())["ok" if r.get("ok") else "failed"] += 1

    rows = []
    for label, c in sorted(tally.items(), key=lambda kv: (-kv[1]["failed"], -sum(kv[1].values()))):
        total = c["ok"] + c["failed"]
        if c["failed"]:
            rows.append((label, f"{c['failed']} failed of {total}"))
        else:
            rows.append((label, "ok" if total == 1 else f"ok ×{total}"))
    return rows[:limit]


def _money(usd: float) -> str:
    return "$0.00" if usd < 0.005 else f"${usd:,.2f}"


# -- the prose layer -------------------------------------------------------
_VOICE = (
    "You are Herald, the part of Orion that writes to its user by email. Orion is this user's "
    "personal knowledge system: it curates their Obsidian vault, keeps a world model of their "
    "life, and runs overnight jobs.\n\n"
    "You are given a JSON block of facts that are already true and already displayed as figures "
    "below your text. Write the opening of the letter: at most two short paragraphs, plain "
    "prose, no headings, no bullet points, no markdown, no greeting, no sign-off.\n\n"
    "Say what actually matters in these numbers — what changed, what is waiting, what is going "
    "wrong. If something needs the user's attention, name it plainly in the first sentence. If "
    "the night was uneventful, say so in one line and stop; do not pad. Never invent a number "
    "or a detail that is not in the JSON. Address the user directly as 'you'. Be dry and "
    "concrete rather than warm."
)


async def _lede(facts: dict[str, Any], weekly: bool = False) -> str:
    """Two paragraphs from deepseek-v4-flash, or "" if that is not available right now.

    Failure here is never fatal: an empty lede simply means the letter opens on its figures.
    """
    if not mailer.settings().get("prose", True):
        return ""
    from orion.core.cognition import Mode
    from orion.core.providers import router
    from orion.core.providers.base import Message

    horizon = "the past week" if weekly else "the last day"
    messages = [
        Message(role="system", content=_VOICE, cacheable=True),
        Message(role="user", content=f"Facts about {horizon}:\n\n"
                                     f"{json.dumps(facts, indent=2, default=str)}"),
    ]
    try:
        chunks = []
        async for token in router.route(Mode.REASONING if weekly else Mode.REFLEX, messages):
            chunks.append(token)
        out = "".join(chunks).strip()
    except Exception as e:
        log.info("herald prose unavailable (%s); sending figures only", e)
        return ""
    # the router yields these as ordinary text when it has nothing to route to
    if not out or out.startswith("[budget]") or out.startswith("[no provider"):
        return ""
    return out[:2000]


# -- the letters -----------------------------------------------------------
def _dateline() -> str:
    return datetime.now().strftime("%A %d %B %Y · %H:%M")


_FOOTER = ("Sent by Herald, Orion's mail agent, from your own machine. Retune what arrives and "
           "when at /agents/herald.")


async def morning_briefing() -> dict:
    """Daily: what ran overnight, what is waiting on you, what it cost."""
    runs = _runs_since(18)
    failures = [r for r in runs if not r.get("ok")]
    items = _inbox()
    stats = _stats()
    spend = _spend(1)
    oldest = max((_age_days(i.get("created_at")) for i in items), default=0.0)

    facts = {
        "waiting_for_you": len(items),
        "waiting_by_agent": dict(_by_agent(items)),
        "oldest_waiting_days": round(oldest, 1),
        "runs_overnight": len(runs),
        # per job, so the prose can say "the index ran hourly" instead of counting runs
        "overnight_by_job": dict(_run_tally(runs, limit=20)),
        "jobs_failed": [{"job": r["label"], "error": str(r.get("result"))[:200]}
                        for r in failures],
        "world_model": stats,
        "cloud_spend_today_usd": spend["cost_usd"],
    }

    sections: list[dict[str, Any]] = []
    if items:
        sections.append({
            "heading": "Waiting for you",
            "rows": [(agent, n) for agent, n in _by_agent(items).most_common()],
            "accent": render.COPPER,
            "note": (f"The oldest has been there {oldest:.0f} days." if oldest >= 1
                     else "All of it arrived in the last day."),
        })
    else:
        sections.append({"heading": "Waiting for you",
                         "blurb": "Nothing. The review queue is empty."})

    if runs:
        # One row per *job*, not per run. The vault index fires hourly, so listing runs
        # individually buried the interesting passes under ten identical lines.
        sections.append({
            "heading": "Overnight",
            "rows": _run_tally(runs),
            "accent": render.FACT if not failures else render.IDEA,
        })
    if failures:
        sections.append({
            "heading": "Went wrong",
            "bullets": [f"{r['label']}: {str(r.get('result'))[:160]}" for r in failures[:6]],
            "accent": render.IDEA,
        })
    if stats:
        sections.append({
            "heading": "World model",
            "rows": [("entities", stats.get("entities", 0)),
                     ("knowledge", stats.get("knowledge", 0)),
                     ("relationships", stats.get("relationships", 0)),
                     ("events", stats.get("events", 0))],
        })
    sections.append({
        "heading": "Cloud spend",
        "rows": [("today", _money(spend["cost_usd"])), ("tokens", f"{spend['tokens']:,}")],
    })

    letter = {
        "eyebrow": "Morning briefing",
        "title": f"Good morning, {_user()}",
        "dateline": _dateline(),
        "lede": await _lede(facts),
        "sections": sections,
        "footer": _FOOTER,
    }
    subject = _briefing_subject(len(items), len(failures))
    result = await mailer.deliver("briefing", subject, render.html(letter), render.text(letter))
    return {**result, "waiting": len(items), "failures": len(failures)}


def _briefing_subject(waiting: int, failures: int) -> str:
    """A subject line that is readable without opening the mail."""
    stamp = datetime.now().strftime("%a %d %b")
    if failures:
        return f"Orion · {stamp} · {failures} job{'s' if failures != 1 else ''} failed overnight"
    if waiting:
        return f"Orion · {stamp} · {waiting} waiting for you"
    return f"Orion · {stamp} · all clear"


async def weekly_letter() -> dict:
    """Weekly: the longer account — what grew, what was curated, what it cost."""
    runs = _runs_since(24 * 7)
    ok = [r for r in runs if r.get("ok")]
    failures = [r for r in runs if not r.get("ok")]
    items = _inbox()
    stats = _stats()
    spend = _spend(7)
    by_job = Counter(r["label"] for r in ok)

    facts = {
        "runs_this_week": len(runs),
        "runs_failed": len(failures),
        "busiest_jobs": dict(by_job.most_common(5)),
        "waiting_for_you": len(items),
        "world_model": stats,
        "cloud_spend_week_usd": spend["cost_usd"],
        "spend_by_model": {k: round(v, 4) for k, v in spend["by_model"].items()},
    }

    sections: list[dict[str, Any]] = [
        {"heading": "What Orion holds",
         "rows": [("entities", stats.get("entities", 0)),
                  ("knowledge items", stats.get("knowledge", 0)),
                  ("relationships", stats.get("relationships", 0)),
                  ("awaiting review", stats.get("pending_reviews", 0))]},
        {"heading": "The week's work",
         "rows": ([(label, n) for label, n in by_job.most_common(8)]
                  or [("nothing ran", 0)]),
         "accent": render.FACT},
    ]
    if failures:
        sections.append({
            "heading": "Trouble",
            "bullets": [f"{label}: {n} failed run{'s' if n != 1 else ''}"
                        for label, n in Counter(r["label"] for r in failures).most_common()],
            "accent": render.IDEA,
        })
    sections.append({
        "heading": "Cloud spend",
        "rows": ([("7 days", _money(spend["cost_usd"]))]
                 + [(model, _money(cost)) for model, cost in
                    sorted(spend["by_model"].items(), key=lambda kv: -kv[1])[:4]]),
        "note": "Background work stays local and costs nothing; only chat and this letter "
                "reach the cloud.",
    })

    letter = {
        "eyebrow": "Weekly letter",
        "title": "The week in your world model",
        "dateline": _dateline(),
        "lede": await _lede(facts, weekly=True),
        "sections": sections,
        "footer": _FOOTER,
    }
    result = await mailer.deliver(
        "weekly", f"Orion · week to {datetime.now():%d %b} · {_money(spend['cost_usd'])} spent",
        render.html(letter), render.text(letter))
    return {**result, "runs": len(runs), "spend_usd": spend["cost_usd"]}


async def watch_alerts() -> dict:
    """Event-driven: mail only when something is actually wrong, and only once per problem.

    Three triggers, all deterministic and cheap enough to run every half hour: a job whose last
    run failed, cloud spend crossing the configured line, and the router's own daily token
    ceiling being hit (which silently pauses cloud calls — exactly the failure you would
    otherwise discover days later).
    """
    cfg = mailer.settings().get("alerts") or {}
    cooldown = float(cfg.get("cooldown_hours", 12) or 12)
    fired: list[dict[str, Any]] = []

    from orion.core.scheduler import scheduler
    c = store.conn()
    try:
        for job in scheduler.jobs():
            history = scheduler.history(job.name)
            if not history:
                continue
            key = f"job_failed:{job.name}"
            if history[0].get("ok"):
                store.clear_alert(c, key)       # green again: the next failure alerts at once
                continue
            if store.alert_due(c, key, cooldown):
                fired.append({"key": key, "heading": f"{job.display_label} failed",
                              "detail": str(history[0].get("result"))[:300],
                              "when": history[0].get("at", "")})

        spend = _spend(1)
        ceiling = float(cfg.get("spend_usd", 0) or 0)
        if ceiling and spend["cost_usd"] >= ceiling:
            key = f"spend:{date.today().isoformat()}"
            if store.alert_due(c, key, cooldown):
                fired.append({"key": key, "heading": "Cloud spend is above your line",
                              "detail": f"{_money(spend['cost_usd'])} today, past the "
                                        f"{_money(ceiling)} you set.",
                              "when": ""})

        from orion.core.providers import router
        usage = router.usage()
        if usage.get("ceiling") and usage.get("fraction", 0) >= 1.0:
            key = f"budget:{date.today().isoformat()}"
            if store.alert_due(c, key, cooldown):
                fired.append({"key": key, "heading": "Daily token ceiling reached",
                              "detail": "Cloud calls are paused until midnight; chat is "
                                        "answering from the local model only.",
                              "when": ""})

        if not fired:
            return {"ok": True, "outcome": "quiet", "alerts": 0}

        letter = {
            "eyebrow": "Alert",
            "title": fired[0]["heading"] if len(fired) == 1 else f"{len(fired)} things need you",
            "dateline": _dateline(),
            "sections": [{"heading": f["heading"],
                          "bullets": [f["detail"]] + ([f"Last run {f['when']}."] if f["when"] else []),
                          "accent": render.IDEA} for f in fired],
            "footer": _FOOTER,
        }
        result = await mailer.deliver(
            "alert", f"Orion alert · {fired[0]['heading']}", render.html(letter),
            render.text(letter))
        if result.get("outcome") == "sent":
            for f in fired:
                store.mark_alerted(c, f["key"], f["detail"])
    finally:
        c.close()
    return {**result, "alerts": len(fired)}


async def nudge_inbox() -> dict:
    """One reminder when the review queue starts to rot — never a daily drip."""
    cfg = mailer.settings().get("nudge") or {}
    after = float(cfg.get("after_days", 3) or 3)
    minimum = int(cfg.get("min_items", 3) or 3)
    cooldown = float(cfg.get("cooldown_hours", 72) or 72)

    stale = [i for i in _inbox() if _age_days(i.get("created_at")) >= after]
    if len(stale) < minimum:
        return {"ok": True, "outcome": "quiet", "stale": len(stale)}

    c = store.conn()
    try:
        if not store.alert_due(c, "nudge:inbox", cooldown):
            return {"ok": True, "outcome": "cooling down", "stale": len(stale)}
    finally:
        c.close()

    oldest = max(_age_days(i.get("created_at")) for i in stale)
    letter = {
        "eyebrow": "Reminder",
        "title": f"{len(stale)} things have been waiting a while",
        "dateline": _dateline(),
        "sections": [
            {"heading": "By agent", "rows": list(_by_agent(stale).most_common()),
             "accent": render.COPPER,
             "note": f"The oldest has sat for {oldest:.0f} days. Nothing is applied until "
                     f"you say so, so these will wait indefinitely."},
            {"heading": "A sample",
             "bullets": [str(i.get("title", ""))[:120] for i in stale[:6]]},
        ],
        "footer": _FOOTER,
    }
    result = await mailer.deliver(
        "nudge", f"Orion · {len(stale)} items still waiting on you",
        render.html(letter), render.text(letter))
    if result.get("outcome") == "sent":
        c = store.conn()
        try:
            store.mark_alerted(c, "nudge:inbox", f"{len(stale)} stale items")
        finally:
            c.close()
    return {**result, "stale": len(stale)}


# -- shared bits -----------------------------------------------------------
def _user() -> str:
    from orion.core import identity
    try:
        return identity.user()
    except Exception:
        return "there"


def compose(kind: str) -> dict[str, Any]:
    """Render a letter without sending it — what ``POST /plugins/herald/preview`` returns.

    Deliberately skips the prose call so previewing the template is free and instant.
    """
    stats, items, spend = _stats(), _inbox(), _spend(1)
    letter = {
        "eyebrow": "Preview", "title": f"Preview · {kind}", "dateline": _dateline(),
        "sections": [
            {"heading": "Waiting for you", "rows": list(_by_agent(items).most_common())
             or [("nothing", 0)], "accent": render.COPPER},
            {"heading": "World model",
             "rows": [("entities", stats.get("entities", 0)),
                      ("knowledge", stats.get("knowledge", 0))]},
            {"heading": "Cloud spend", "rows": [("today", _money(spend["cost_usd"]))]},
        ],
        "footer": _FOOTER,
    }
    return {"subject": f"Orion · preview · {kind}", "html": render.html(letter),
            "text": render.text(letter)}
