"""Identity & permissions — single-user, with human-approval gates.

Orion is built for one user. The security posture that matters is not multi-tenant auth but
the constitution's rule: *ask before irreversible actions*. This module decides when an action
needs explicit approval before a tool runs.
"""
from __future__ import annotations

from orion.core.config import config
from orion.core.constitution import constitution


def user() -> str:
    return config.section("settings").get("identity", {}).get("user", "user")


def needs_approval(action: str) -> bool:
    """True if the action is irreversible and must be confirmed by the user first."""
    return constitution.requires_approval(action)
