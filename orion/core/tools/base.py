"""Tool abstraction. Concrete tools live in plugins, not core.

Carried from the MVP (cleaned): a tool declares triggers, an args schema, whether it needs
confirmation, and a dispatch mode. Tool execution is always local-only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    output: str
    meta: dict[str, Any] = field(default_factory=dict)

    def format_for_context(self) -> str:
        return f"<tool_result ok={self.ok}>\n{self.output}\n</tool_result>"


class BaseTool(ABC):
    name: str = "base"
    description: str = ""
    triggers: tuple[str, ...] = ()
    args_schema: dict[str, Any] = {}
    requires_confirm: bool = False          # irreversible actions gate through identity/approval
    dispatch_mode: str = "extract"          # "extract" (args in message) | "generate" (model composes)

    @abstractmethod
    async def run(self, args: dict[str, Any]) -> ToolResult: ...
