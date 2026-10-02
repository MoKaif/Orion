"""Bounded, read-only IMAP source. No message flags are changed and raw bodies are not stored."""
from __future__ import annotations

import hashlib
import imaplib
import os
import re
from datetime import datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from typing import Any

from orion.core.config import config


def _text(message) -> str:
    parts = []
    candidates = list(message.walk()) if message.is_multipart() else [message]
    for part in candidates:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        if part.get_content_type() == "text/plain":
            try:
                parts.append(part.get_content())
            except Exception:
                continue
    if not parts:
        for part in candidates:
            if part.get_content_type() == "text/html" and not part.get_filename():
                try:
                    parts.append(re.sub(r"<[^>]+>", " ", part.get_content()))
                except Exception:
                    pass
                break
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def fetch() -> list[dict[str, str]]:
    cfg = config.section("chronos").get("mail", {})
    if not cfg.get("enabled", True):
        return []
    address = (os.environ.get("GMAIL_ADDRESS") or "").strip()
    password = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    if not address or not password:
        raise RuntimeError("GMAIL_ADDRESS/GMAIL_APP_PASSWORD are not configured")
    since = (datetime.now() - timedelta(days=max(1, int(cfg.get("lookback_days", 14))))).strftime("%d-%b-%Y")
    limit = max(1, min(100, int(cfg.get("max_messages", 30))))
    max_bytes = max(8192, min(262144, int(cfg.get("max_bytes", 65536))))
    host = str(cfg.get("host", "imap.gmail.com"))
    out = []
    with imaplib.IMAP4_SSL(host, 993, timeout=20) as client:
        client.login(address, password)
        ok, selected = client.select("INBOX", readonly=True)
        if ok != "OK":
            raise RuntimeError("Gmail INBOX is unavailable")
        uid_validity = ""
        status, data = client.status("INBOX", "(UIDVALIDITY)")
        if status == "OK" and data:
            uid_validity = data[0].decode(errors="ignore")
        ok, data = client.uid("search", None, "SINCE", since)
        if ok != "OK":
            raise RuntimeError("Gmail search failed")
        for uid in (data[0].split() if data else [])[-limit:]:
            ok, chunks = client.uid("fetch", uid, f"(BODY.PEEK[]<0.{max_bytes}>)")
            if ok != "OK":
                continue
            raw = next((chunk[1] for chunk in chunks if isinstance(chunk, tuple)), b"")
            if not raw:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw)
            message_id = str(message.get("Message-ID") or "").strip()
            fingerprint = hashlib.sha256(
                f"{uid_validity}:{uid.decode()}:{message_id}".encode()).hexdigest()
            out.append({
                "fingerprint": fingerprint,
                "message_id": message_id,
                "from": str(message.get("From") or "")[:500],
                "subject": str(make_header(decode_header(str(message.get("Subject") or ""))))[:500],
                "date": str(message.get("Date") or "")[:200],
                "text": _text(message)[:12000],
            })
    return out
