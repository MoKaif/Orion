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
def address() -> str:
    """The Gmail account Herald sends *from*, and by default *to*."""
    return (os.environ.get("GMAIL_ADDRESS") or "").strip()


def _password() -> str:
    """The 16-character app password. Google prints it in groups of four; strip the spaces."""
    return (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()


def configured() -> bool:
    return bool(address() and _password())


def status() -> dict:
    """What the UI needs to explain itself when Herald cannot send."""
    if not settings().get("enabled", True):
        return {"ok": False, "reason": "Herald is switched off in config/herald.json."}
    if not address():
        return {"ok": False, "reason": "No GMAIL_ADDRESS in config/secrets.json."}
    if not _password():
        return {"ok": False, "reason": "No GMAIL_APP_PASSWORD in config/secrets.json."}
    return {"ok": True, "reason": "", "from": address(), "to": recipient()}


def recipient() -> str:
    """Where digests go — configured address, else the account itself."""
    return (settings().get("to") or "").strip() or address()


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
    """True when this address is the account Herald sends from (the unattended-send path)."""
    me = _normalize(address())
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
                                    row["html"], row["text"])
        except Exception as e:
            store.mark_failed(c, mid, f"{type(e).__name__}: {e}")
            log.warning("herald send failed for message %d: %s", mid, e)
            return {"ok": False, "outcome": "failed", "id": mid, "reason": str(e)}
        store.mark_sent(c, mid)
    finally:
        c.close()
    log.info("herald sent message %d", mid)
    return {"ok": True, "outcome": "sent", "id": mid}


def _smtp_send(to_addr: str, subject: str, html: str, text: str) -> None:
    """One blocking SMTP conversation. Runs in a worker thread — never on the event loop.

    Sends ``multipart/alternative``: the plain-text part is the real message, the HTML part is
    the same content dressed. A client that refuses HTML still gets something readable.
    """
    sender = address()
    msg = EmailMessage()
    msg["From"] = formataddr((settings().get("from_name", "Orion"), sender))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="orion.local")
    msg["Auto-Submitted"] = "auto-generated"      # keeps vacation responders from replying
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL(_HOST, _PORT, timeout=_TIMEOUT,
                          context=ssl.create_default_context()) as s:
        s.login(sender, _password())
        s.send_message(msg)
