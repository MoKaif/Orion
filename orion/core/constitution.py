"""The constitution: the governing document every specialist and LLM call inherits.

Core principles are immutable and enforced in code where possible. Communication style is the
only part allowed to evolve (adaptive style, stable values). See docs/ORION_MANIFESTO.md.
"""
from __future__ import annotations

from pathlib import Path

_TEXT_PATH = Path(__file__).with_name("constitution.md")

# Enforced in code, not merely prompted. Actions matching these require human approval.
IRREVERSIBLE_ACTIONS = frozenset(
    {"send_email", "delete_file", "spend_money", "book_travel",
     "cancel_subscription", "share_document", "shell", "merge_pr"}
)


class Constitution:
    def __init__(self, text_path: Path = _TEXT_PATH):
        self._text = text_path.read_text() if text_path.exists() else ""

    def text(self) -> str:
        return self._text

    def system_preamble(self) -> str:
        """Prefix injected into every LLM system prompt."""
        return "You are Orion. You operate under this constitution:\n\n" + self._text

    @staticmethod
    def requires_approval(action: str) -> bool:
        return action in IRREVERSIBLE_ACTIONS


constitution = Constitution()
