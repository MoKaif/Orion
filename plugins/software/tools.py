"""Software plugin tools — read_file (safe) and shell (confirm-gated, local-only)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from orion.core.config import config
from orion.core.tools.base import BaseTool, ToolResult

_MAX = 20000


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file on the local machine by path."
    triggers = ("read file", "open file", "show file", "cat ")
    args_schema = {"path": "str"}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        path = args.get("path", "").strip()
        if not path:
            return ToolResult(False, "no path provided")
        p = Path(path).expanduser()
        if not p.is_file():
            return ToolResult(False, f"not a file: {path}")
        try:
            return ToolResult(True, p.read_text(encoding="utf-8", errors="ignore")[:_MAX],
                              meta={"path": str(p)})
        except OSError as e:
            return ToolResult(False, f"read failed: {e}")


class ShellTool(BaseTool):
    name = "shell"
    description = "Run a shell command on the local machine. Requires user confirmation."
    triggers = ("run command", "execute", "shell", "run ")
    args_schema = {"command": "str"}
    requires_confirm = True          # gated by identity/approval — the constitution's rule
    dispatch_mode = "generate"       # the model composes the command

    async def run(self, args: dict[str, Any]) -> ToolResult:
        command = (args.get("command") or "").strip()
        if not command:
            return ToolResult(False, "no command provided")
        blocked = config.section("tools").get("shell", {}).get("blocked_commands", [])
        if any(b and b in command for b in blocked):
            return ToolResult(False, f"blocked command: {command!r}")
        timeout = config.section("tools").get("shell", {}).get("timeout_seconds", 20)
        try:
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return ToolResult(True, out.decode(errors="ignore")[:_MAX],
                              meta={"exit_code": proc.returncode})
        except asyncio.TimeoutError:
            return ToolResult(False, f"command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(False, f"execution failed: {e}")
