"""Transport and send policy — the only place in Orion that opens a socket to the outside world.

Two rules govern every message, and they are enforced here rather than trusted to callers:

**Who it is addressed to decides whether it needs you.** Mail to the account's own address is
Orion talking to itself in another window — a briefing you must approve is a briefing you will
stop reading — so it sends unattended. Mail to *anyone else* is held in the outbox until you
release it, which is the constitution's irreversible-action rule (``send_email`` has been in
``IRREVERSIBLE_ACTIONS`` since before Herald existed) applied where it actually bites.

**Herald must never become noise.** A daily ceiling and quiet hours are checked before the
socket opens, not after composition, so a runaway job wastes nothing. Alerts deliberately ignore
quiet hours: a job that died at 02:00 is worth waking up to, a summary of it is not.

Credentials come from the environment only (``config.Config`` seeds it from the gitignored
``config/secrets.json``). No key ⇒ ``configured()`` is False, every job no-ops with an honest
reason, and nothing raises.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from orion.core.config import config

from . import store

log = logging.getLogger("orion.herald")

_HOST = "smtp.gmail.com"
_PORT = 465                       # implicit TLS; no STARTTLS upgrade window to get wrong
_TIMEOUT = 30


def settings() -> dict:
    """``config/herald.json`` with this machine's ``herald.local.json`` merged over it."""
    return config.section("herald")


# -- credentials -----------------------------------------------------------
# Sending identity and owning identity are deliberately separate. When they are the same
# mailbox, Gmail files the result as something you sent yourself — your own avatar, "me" in
# the sender column, no importance signal to learn from. Pointing GMAIL_SENDER_ADDRESS at a
# second (free) Google account makes Herald a correspondent instead of an echo, and is the
# only thing that actually fixes it: Gmail's SMTP rewrites a From header that isn't the
# authenticated account, so no amount of header dressing can fake a different sender.
def owner_address() -> str:
    """The user's own mailbox — what "addressed to you" means, and the default recipient."""
    return (os.environ.get("GMAIL_ADDRESS") or "").strip()


def sender_address() -> str:
    """The account Herald authenticates and sends as. Falls back to the owner's own."""
    return (os.environ.get("GMAIL_SENDER_ADDRESS") or "").strip() or owner_address()


def _password() -> str:
    """The 16-character app password. Google prints it in groups of four; strip the spaces."""
    key = ("GMAIL_SENDER_APP_PASSWORD" if os.environ.get("GMAIL_SENDER_ADDRESS")
           else "GMAIL_APP_PASSWORD")
    return (os.environ.get(key) or "").replace(" ", "").strip()


#: Kept as the old name so nothing outside this module has to care which identity it meant.
address = sender_address


def configured() -> bool:
    return bool(sender_address() and _password())


def dedicated_sender() -> bool:
    """True when Herald has its own account, so its mail is not self-addressed."""
    return _normalize(sender_address()) != _normalize(owner_address())


def status() -> dict:
    """What the UI needs to explain itself when Herald cannot send."""
    if not settings().get("enabled", True):
        return {"ok": False, "reason": "Herald is switched off in config/herald.json."}
    if not owner_address():
        return {"ok": False, "reason": "No GMAIL_ADDRESS in config/secrets.json."}
    if not _password():
        missing = ("GMAIL_SENDER_APP_PASSWORD" if os.environ.get("GMAIL_SENDER_ADDRESS")
                   else "GMAIL_APP_PASSWORD")
        return {"ok": False, "reason": f"No {missing} in config/secrets.json."}
    return {"ok": True, "reason": "", "from": sender_address(), "to": recipient(),
            "dedicated_sender": dedicated_sender()}


def recipient() -> str:
    """Where digests go — configured address, else the user's own account."""
    return (settings().get("to") or "").strip() or owner_address()


# -- recipient identity ----------------------------------------------------
def _normalize(addr: str) -> str:
    """Gmail's own equivalence rules, so ``k.a.i.f+orion@gmail.com`` is still you.

    Gmail ignores dots in the local part and everything after a ``+``. Getting this wrong in the
    permissive direction would let a lookalike address send unattended, so it only ever collapses
    forms Google itself treats as the same mailbox — and only for Google's own domains.
    """
    addr = (addr or "").strip().lower()
    m = re.match(r"^([^@]+)@(.+)$", addr)
    if not m:
        return addr
    local, domain = m.groups()
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def is_self(addr: str) -> bool:
    """True when this address is the *user's own* mailbox — the unattended-send path.

    Deliberately keyed to the owner rather than the sender. Once Herald can send from its own
    account, "who it comes from" and "who it may be sent to without asking" are different
    questions, and only the second one is a safety gate: mail addressed to Herald's own sending
    account is not mail to you, and must still be held.
    """
    me = _normalize(owner_address())
    return bool(me) and _normalize(addr) == me


# -- policy ----------------------------------------------------------------
def _in_quiet_hours(at: datetime | None = None) -> bool:
    window = settings().get("quiet_hours") or []
    if len(window) != 2:
        return False
    start, end = int(window[0]), int(window[1])
    hour = (at or datetime.now()).hour
    # a window that crosses midnight (22 → 7) is the normal case
    return start <= hour or hour < end if start > end else start <= hour < end


def may_send(kind: str) -> tuple[bool, str]:
    """Whether a message of this kind may go out right now. Never raises; explains itself."""
    st = status()
    if not st["ok"]:
        return False, st["reason"]
    cap = int(settings().get("daily_cap", 6) or 0)
    if cap:
        c = store.conn()
        try:
            sent = store.sent_since(c, 24)
        finally:
            c.close()
        if sent >= cap:
            return False, f"daily cap reached ({sent}/{cap} in the last 24h)"
    if kind != "alert" and _in_quiet_hours():
        return False, "quiet hours"
    return True, ""


# -- delivery --------------------------------------------------------------
async def deliver(kind: str, subject: str, html: str, text: str,
                  to: str | None = None) -> dict:
    """Record a message, then send it if policy allows and it is addressed to you.

    Returns a dict describing what happened — ``sent`` · ``held`` (waiting on your approval) ·
    ``queued`` (policy said not now) · ``failed``. Callers treat all four as normal outcomes.
    """
    to_addr = (to or recipient()).strip()
    if not to_addr:
        return {"ok": False, "outcome": "failed", "reason": "no recipient configured"}

    c = store.conn()
    try:
        if not is_self(to_addr):
            mid = store.queue(c, kind, to_addr, subject, html, text, status="held",
                              reason="addressed outside your own account")
            log.info("herald holding message %d for approval (to %s)", mid, to_addr)
            return {"ok": True, "outcome": "held", "id": mid, "to": to_addr}

        allowed, why = may_send(kind)
        if not allowed:
            mid = store.queue(c, kind, to_addr, subject, html, text, status="queued", reason=why)
            log.info("herald not sending %s now (%s)", kind, why)
            return {"ok": False, "outcome": "queued", "id": mid, "reason": why}

        mid = store.queue(c, kind, to_addr, subject, html, text)
    finally:
        c.close()

    return await _send_row(mid)


async def send_held(mid: int) -> dict:
    """Release a held message — the user pressed the button. Policy caps still apply."""
    c = store.conn()
    try:
        row = store.get(c, mid)
        if row is None:
            return {"ok": False, "reason": f"no message {mid}"}
        if row["status"] not in ("held", "queued", "failed"):
            return {"ok": False, "reason": f"message {mid} is already {row['status']}"}
        allowed, why = may_send(row["kind"])
        if not allowed:
            store.set_status(c, mid, row["status"], why)
            return {"ok": False, "outcome": row["status"], "reason": why}
    finally:
        c.close()
    return await _send_row(mid)


def cancel(mid: int) -> dict:
    """Drop a message without sending it."""
    c = store.conn()
    try:
        row = store.get(c, mid)
        if row is None:
            return {"ok": False, "reason": f"no message {mid}"}
        if row["status"] == "sent":
            return {"ok": False, "reason": "already sent — mail cannot be recalled"}
        store.set_status(c, mid, "cancelled", "you declined it")
    finally:
        c.close()
    return {"ok": True, "outcome": "cancelled", "id": mid}


async def _send_row(mid: int) -> dict:
    c = store.conn()
    try:
        row = store.get(c, mid)
        if row is None:
            return {"ok": False, "reason": f"no message {mid}"}
        try:
            await asyncio.to_thread(_smtp_send, row["to_addr"], row["subject"],
                                    row["html"], row["text"], row["kind"])
        except Exception as e:
            store.mark_failed(c, mid, f"{type(e).__name__}: {e}")
            log.warning("herald send failed for message %d: %s", mid, e)
            return {"ok": False, "outcome": "failed", "id": mid, "reason": str(e)}
        store.mark_sent(c, mid)
    finally:
        c.close()
    log.info("herald sent message %d", mid)
    return {"ok": True, "outcome": "sent", "id": mid}


def _high_priority(kind: str) -> bool:
    """Which letters claim priority. Alerts and nudges act on you; summaries do not."""
    return kind in (settings().get("high_priority") or ["alert", "nudge"])


def _smtp_send(to_addr: str, subject: str, html: str, text: str, kind: str = "manual") -> None:
    """One blocking SMTP conversation. Runs in a worker thread — never on the event loop.

    Sends ``multipart/alternative``: the plain-text part is the real message, the HTML part is
    the same content dressed. A client that refuses HTML still gets something readable.

    On headers: the priority set is honoured by most desktop clients but **not** by Gmail's
    importance markers, which are learned per-user and cannot be asserted by a sender — a Gmail
    filter is the only thing that reliably marks these important. ``Auto-Submitted`` used to be
    set here and is not any more: its only benefit was suppressing vacation auto-responders,
    which is meaningless for mail you send yourself, while marking a message machine-generated
    is exactly the signal that argues *against* the importance we want.
    """
    sender = sender_address()
    msg = EmailMessage()
    msg["From"] = formataddr((settings().get("from_name", "Orion · Herald"), sender))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="orion.local")
    if _high_priority(kind):
        msg["X-Priority"] = "1 (Highest)"
        msg["Importance"] = "High"
        msg["Priority"] = "urgent"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL(_HOST, _PORT, timeout=_TIMEOUT,
                          context=ssl.create_default_context()) as s:
        s.login(sender, _password())
        s.send_message(msg)
