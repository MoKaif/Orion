"""Guardian — backups and operational health, with no model calls."""
from __future__ import annotations

from html import escape

from fastapi import APIRouter

from orion.core import plugin_sdk as orion

from . import service

router = APIRouter()


def register() -> None:
    orion.add_agent(
        "guardian", "Guardian", tagline="Backups & health",
        blurb="Protects Orion's durable state and reports operational trouble without using AI tokens.",
        icon="shield-check", accent="observation", plugin="guardian", order=10,
        summary=_summary, detail=_detail,
    )
    orion.add_job("guardian_backup", "15 2 * * *", service.create_backup,
                  preemptible=False, agent="guardian", label="State snapshot",
                  description="Back up Orion's SQLite databases and non-secret configuration.")
    orion.add_job("guardian_audit", "15 */6 * * *", service.audit,
                  agent="guardian", label="Integrity audit",
                  description="Check databases, configuration, backup freshness, and disk space.")
    orion.add_widget("guardian_status", "Guardian", _widget, plugin="guardian")
    orion.add_report_source("guardian", alerts=_alerts, plugin="guardian")


def _summary() -> dict:
    state = service.audit()
    return {"pending": len(state["problems"]), "metrics": [
        {"label": "backups", "value": state["backup_count"]},
        {"label": "free disk", "value": f"{state['free_gb']:.1f} GiB"},
        {"label": "status", "value": "healthy" if state["ok"] else "attention"},
    ]}


def _detail() -> dict:
    return {"guardian": service.audit(), "backups": service.backups()[:12]}


def _alerts() -> list[dict]:
    state = service.audit()
    if state["ok"]:
        return [{"key": "guardian_health", "resolved": True}]
    return [{"key": "guardian_health", "heading": "Guardian found a state problem",
             "detail": "; ".join(state["problems"][:4])}]


def _widget() -> str:
    state = service.audit()
    if state["ok"]:
        latest = state.get("latest_backup") or {}
        return (f'<p class="empty">Healthy · {state["databases_checked"]} databases checked · '
                f'latest backup {escape(str(latest.get("name") or "—"))}</p>')
    items = "".join(f'<li class="ws"><span class="ws-name">{escape(p)}</span></li>'
                    for p in state["problems"][:4])
    return f'<ul class="ws-list">{items}</ul>'


@router.get("/status")
async def guardian_status():
    return service.audit()


@router.get("/backups")
async def guardian_backups():
    return service.backups()


@router.post("/backup")
async def guardian_backup():
    return service.create_backup()
