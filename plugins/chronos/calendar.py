"""Google Calendar connector; reads freely, writes only after an Inbox approval."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orion.core.config import config

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def settings() -> dict[str, Any]:
    return config.section("chronos")


def _config_dir() -> Path:
    return config.root() / "config"


def client_file() -> Path | None:
    pattern = str(settings().get("calendar", {}).get("client_glob", "client_secret_*.json"))
    return next(iter(sorted(_config_dir().glob(pattern))), None)


def token_file() -> Path:
    name = str(settings().get("calendar", {}).get("token_file", "google-calendar-token.json"))
    return _config_dir() / name


def _credentials():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError("Google Calendar libraries are not installed") from exc
    path = token_file()
    if not path.exists():
        raise RuntimeError("Google Calendar authorization is waiting; run scripts/authorize_chronos.py")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
        path.chmod(0o600)
    if not creds.valid:
        raise RuntimeError("Google Calendar authorization is invalid or expired")
    return creds


def authorized() -> tuple[bool, str]:
    if client_file() is None:
        return False, "OAuth client JSON is missing from config/"
    try:
        _credentials()
        return True, "Google Calendar authorized"
    except Exception as exc:
        return False, str(exc)


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Google Calendar libraries are not installed") from exc
    return build("calendar", "v3", credentials=_credentials(), cache_discovery=False)


def list_events() -> list[dict[str, Any]]:
    cfg = settings()
    cal_id = str(cfg.get("calendar", {}).get("id", "primary"))
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=max(1, int(cfg.get("horizon_days", 31))))
    result = _service().events().list(
        calendarId=cal_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=250,
    ).execute()
    out = []
    for raw in result.get("items", []):
        if raw.get("status") == "cancelled" or not raw.get("id"):
            continue
        out.append({
            "google_id": raw["id"],
            "summary": raw.get("summary") or "Busy",
            "start_at": raw.get("start", {}).get("dateTime") or raw.get("start", {}).get("date"),
            "end_at": raw.get("end", {}).get("dateTime") or raw.get("end", {}).get("date"),
            "location": raw.get("location"), "html_link": raw.get("htmlLink"),
            "updated_at": raw.get("updated"),
            "payload": {"status": raw.get("status"), "all_day": "date" in raw.get("start", {})},
        })
    return [item for item in out if item["start_at"]]


def create_event(proposal: dict[str, Any]) -> dict[str, Any]:
    cfg = settings()
    zone = str(cfg.get("timezone", "Asia/Kolkata"))
    start = proposal["start_at"]
    end = proposal.get("end_at")
    body: dict[str, Any] = {
        "summary": proposal["title"],
        "description": (proposal.get("description") or "") +
                       "\n\nAdded by Orion Chronos after explicit approval.",
        "visibility": "private",
    }
    if proposal.get("location"):
        body["location"] = proposal["location"]
    if "T" in start:
        if not end:
            end = (datetime.fromisoformat(start.replace("Z", "+00:00")) +
                   timedelta(hours=1)).isoformat()
        body["start"] = {"dateTime": start, "timeZone": zone}
        body["end"] = {"dateTime": end, "timeZone": zone}
    else:
        body["start"] = {"date": start[:10]}
        body["end"] = {"date": (end or (datetime.fromisoformat(start[:10]) +
                                          timedelta(days=1)).date().isoformat())[:10]}
    event = _service().events().insert(
        calendarId=str(cfg.get("calendar", {}).get("id", "primary")),
        body=body, sendUpdates="none",
    ).execute()
    return {"id": event.get("id"), "html_link": event.get("htmlLink")}


def authorize() -> Path:
    """Interactive host-side flow used by scripts/authorize_chronos.py."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before authorizing Chronos") from exc
    client = client_file()
    if client is None:
        raise RuntimeError("No client_secret_*.json found in config/")
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=False,
                                  authorization_prompt_message="Open this URL to authorize Chronos:\n{url}")
    path = token_file()
    path.write_text(creds.to_json())
    path.chmod(0o600)
    return path
