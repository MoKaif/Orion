"""Herald — Orion's voice outside the browser.

The other two agents work on things you already have: the Conductor keeps the world model in
order, the Curator edits your vault. Herald is the first that reaches *you* — on a headless
always-on machine, a briefing that only exists at 127.0.0.1:8000 is a briefing nobody reads.

Four letters, all outbound over Gmail SMTP (Herald never reads your mail):

  * morning briefing — what ran overnight, what is waiting, what it cost
  * alerts           — a job failed, spend crossed your line, the token ceiling hit
  * weekly letter    — the longer account of what grew
  * nudges           — one reminder when the review queue starts to rot

Sending is the one irreversible thing Orion does, so the gate is drawn by recipient: mail to
your own account goes unattended (an approval prompt on your own briefing is theatre), and mail
to anyone else — including anything the ``send_email`` tool composes mid-chat — waits in the
inbox with the full text visible until you release it. ``send_email`` was already in the
constitution's ``IRREVERSIBLE_ACTIONS`` before this plugin existed; this is what enforces it.

Everything rides on ``orion.core.plugin_sdk``; the module-level ``router`` mounts at
/plugins/herald.
"""
from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from orion.core import plugin_sdk as orion

router = APIRouter()


def register() -> None:
    from . import digest

    orion.add_agent(
        "herald", "Herald",
        tagline="Mail",
        blurb="Carries Orion out of the browser. Writes you a briefing each morning, a letter "
              "each week, and speaks up when a job fails or spend crosses your line. It only "
              "ever sends — it never reads your mail — and anything addressed outside your own "
              "account waits for you to release it.",
        icon="mail", accent="fact", plugin="herald", order=30,
        summary=_summary, detail=_detail)

    orion.add_job("morning_briefing", "30 7 * * *", digest.morning_briefing, agent="herald",
                  label="Morning briefing",
                  description="What ran overnight, what is waiting on you, and what yesterday "
                              "cost. Sent even when the news is 'all clear'.")
    orion.add_job("herald_alerts", "*/30 * * * *", digest.watch_alerts, agent="herald",
                  label="Watch for trouble",
                  description="Checks for failed jobs, spend above your line, and a hit token "
                              "ceiling. Mails once per problem, not once per check.")
    orion.add_job("weekly_letter", "0 9 * * 1", digest.weekly_letter, agent="herald",
                  label="Weekly letter",
                  description="Monday morning: what the world model grew by, which passes did "
                              "the work, and the week's cloud spend.")
    orion.add_job("inbox_nudge", "0 19 * * *", digest.nudge_inbox, agent="herald",
                  label="Inbox nudge",
                  description="Reminds you once when review items have been sitting for days. "
                              "Stays quiet if the queue is fresh or you were nudged recently.")

    orion.add_tool(SendEmailTool())
    orion.add_widget("herald_mail", "Herald", _render_widget, plugin="herald")
    orion.add_inbox_source("herald", _inbox_items, plugin="herald")


# -- what mission control shows for this agent -----------------------------
def _summary() -> dict:
    from . import mailer, store

    c = store.conn()
    try:
        counts = store.counts(c)
    finally:
        c.close()
    st = mailer.status()
    return {
        "pending": counts.get("held", 0),
        "metrics": [
            {"label": "sent", "value": counts.get("sent", 0)},
            {"label": "last 24h", "value": counts.get("sent_24h", 0)},
            {"label": "held", "value": counts.get("held", 0)},
            {"label": "status", "value": "ready" if st["ok"] else "no key"},
        ],
    }


def _detail() -> dict:
    """The agent's page: its mail log, anything held, and whether it can send at all."""
    from . import mailer, store

    c = store.conn()
    try:
        return {"mail": store.recent(c, 25), "held": store.held(c),
                "mailer": mailer.status()}
    finally:
        c.close()


# -- Herald's share of the inbox -------------------------------------------
def _inbox_items() -> list[dict[str, Any]]:
    """Held messages. The whole text is on the card: you approve what you can read."""
    from . import store

    c = store.conn()
    try:
        pending = store.held(c)
    finally:
        c.close()

    items = []
    for m in pending:
        items.append({
            "origin": "herald", "id": m["id"],
            "title": f"Herald wants to email {m['to_addr']}",
            "body": (m.get("text") or "")[:1200],
            "effect": (f"Sends this message to {m['to_addr']} from your Gmail account. Mail "
                       f"cannot be recalled once it leaves."),
            "created_at": m.get("created_at") or "",
            "prov_agent": "Herald",
            "prov_label": m.get("subject") or "an outgoing message",
            "action_url": f"/plugins/herald/outbox/{m['id']}",
            "actions": [
                orion.inbox_action("Send it", "send", "accept",
                                   confirm=f"Send this to {m['to_addr']}?"),
                orion.inbox_action("Don't send", "cancel", "reject"),
            ],
        })
    return items


# -- the tool the orchestrator can select ----------------------------------
class SendEmailTool(orion.BaseTool):
    """Compose a message from a chat turn.

    ``requires_confirm`` gates it at the orchestrator, and ``mailer.deliver`` gates it again at
    the recipient — belt and braces, because this is the one tool whose mistakes are public.
    """

    name = "send_email"
    description = ("Send an email. Use for reminders, summaries or notes the user asks to be "
                   "mailed. Mail to the user's own address goes immediately; anything else "
                   "waits in their inbox for approval.")
    triggers = ("email", "e-mail", "mail me", "send me", "send an email", "message me")
    args_schema = {
        "to": "recipient address; omit to send to the user's own account",
        "subject": "subject line",
        "body": "the message, in plain text",
    }
    requires_confirm = True
    dispatch_mode = "generate"

    async def run(self, args: dict[str, Any]) -> orion.ToolResult:
        from . import digest, mailer, render

        subject = (args.get("subject") or "A note from Orion").strip()
        body = (args.get("body") or "").strip()
        if not body:
            return orion.ToolResult(False, "No message body to send.")

        letter = {"eyebrow": "From your chat", "title": subject,
                  "dateline": digest._dateline(),
                  "sections": [{"heading": "Message", "bullets": [
                      line for line in body.splitlines() if line.strip()]}],
                  "footer": digest._FOOTER}
        result = await mailer.deliver("manual", subject, render.html(letter),
                                      render.text(letter), to=args.get("to"))

        outcome = result.get("outcome")
        if outcome == "sent":
            return orion.ToolResult(True, f"Sent “{subject}” to {mailer.recipient()}.", result)
        if outcome == "held":
            return orion.ToolResult(
                True, f"Composed “{subject}” for {result.get('to')}. It is waiting in your "
                      f"inbox — nothing leaves until you release it.", result)
        return orion.ToolResult(False, f"Not sent: {result.get('reason', 'unknown reason')}",
                                result)


# -- plugin API (mounted at /plugins/herald) -------------------------------
class OutboxAction(BaseModel):
    action: str          # send | cancel


class TestMail(BaseModel):
    to: str | None = None


@router.get("/status")
async def herald_status():
    """Can Herald send, and as whom? The honest answer when no key is configured."""
    from . import mailer
    return mailer.status()


@router.get("/outbox")
async def list_outbox(status: str | None = None, limit: int = 25):
    from . import store
    c = store.conn()
    try:
        return store.recent(c, limit, status)
    finally:
        c.close()


@router.get("/outbox/{mid}")
async def read_message(mid: int):
    from . import store
    c = store.conn()
    try:
        return store.get(c, mid) or {"error": f"no message {mid}"}
    finally:
        c.close()


@router.post("/outbox/{mid}")
async def resolve_message(mid: int, body: OutboxAction):
    """Release or drop a held message. This is the button on the inbox card."""
    from . import mailer
    if body.action in ("send", "accept", "apply"):
        return await mailer.send_held(mid)
    return mailer.cancel(mid)


@router.post("/preview")
async def preview(kind: str = "briefing"):
    """Render a letter without sending it — iterate on the template for free."""
    from . import digest
    return digest.compose(kind)


@router.post("/test")
async def test_mail(body: TestMail):
    """Prove the credentials work, end to end, without waiting for 07:30."""
    from . import digest, mailer, render
    letter = {
        "eyebrow": "Test", "title": "Herald is wired up",
        "dateline": digest._dateline(),
        "sections": [{"heading": "What this proves",
                      "bullets": ["Your Gmail app password works.",
                                  "Orion can reach smtp.gmail.com on port 465.",
                                  "The briefing that arrives each morning will look like this."]}],
        "footer": digest._FOOTER,
    }
    return await mailer.deliver("manual", "Orion · Herald test", render.html(letter),
                                render.text(letter), to=body.to)


@router.post("/send/{kind}")
async def send_letter(kind: str):
    """Compose and send one letter now: briefing | weekly | alerts | nudge."""
    from . import digest
    fns = {"briefing": digest.morning_briefing, "weekly": digest.weekly_letter,
           "alerts": digest.watch_alerts, "nudge": digest.nudge_inbox}
    if kind not in fns:
        return {"ok": False, "reason": f"unknown letter '{kind}'"}
    return await fns[kind]()


# -- dashboard widget ------------------------------------------------------
def _render_widget() -> str:
    from . import mailer, store

    st = mailer.status()
    if not st["ok"]:
        return (f'<p class="empty">Herald is not sending yet — {escape(st["reason"])} '
                "Add it and Orion starts writing to you.</p>")

    c = store.conn()
    try:
        counts = store.counts(c)
        recent = store.recent(c, 3)
    finally:
        c.close()

    held = counts.get("held", 0)
    rows = "".join(
        f'<li class="ws"><span class="ws-name">{escape(m["subject"])}</span>'
        f'<span class="ws-facts">{escape(m["status"])}</span></li>'
        for m in recent)
    head = (f'<li class="ws"><span class="ws-name">{counts.get("sent_24h", 0)} sent in the '
            f'last day</span><span class="ws-facts">to {escape(st["to"])}</span></li>')
    waiting = (f'<li class="ws"><span class="ws-name">{held} message'
               f'{"s" if held != 1 else ""} waiting on you</span>'
               f'<span class="ws-facts">approval</span></li>' if held else "")
    more = '<a class="btn link-more" href="/agents/herald">the mail log →</a>'
    return f'<ul class="ws-list">{head}{waiting}{rows}</ul>{more}'
