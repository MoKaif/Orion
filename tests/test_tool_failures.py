"""Prevent tool dispatch failures from becoming fabricated successful chat answers."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.core import orchestrator
from orion.core.cognition import Mode


class ToolFailureTests(unittest.IsolatedAsyncioTestCase):
    async def run_tool(self, args, failure=None):
        tool = SimpleNamespace(name="vault_read", requires_confirm=False,
                               run=AsyncMock(side_effect=failure))
        ctx = orchestrator.Context("read journal", Mode.REFLEX, SimpleNamespace())
        events = []
        with (patch.object(orchestrator.selection, "select_tools", return_value=[tool]),
              patch.object(orchestrator.registry, "match", return_value=tool),
              patch.object(orchestrator.dispatcher, "extract_args", AsyncMock(return_value=args)),
              patch.object(orchestrator.identity, "needs_approval", return_value=False)):
            await orchestrator._maybe_run_tool(ctx, events.append, 1)
        return ctx, events, tool

    async def test_missing_arguments_are_visible_without_executing(self):
        ctx, events, tool = await self.run_tool(None)
        tool.run.assert_not_awaited()
        self.assertIn("NOT executed", ctx.tool_result)
        self.assertFalse(events[0]["ok"])

    async def test_tool_exception_becomes_visible_failure(self):
        ctx, events, _ = await self.run_tool({}, RuntimeError("test"))
        self.assertIn("completion is unconfirmed", ctx.tool_result)
        self.assertFalse(events[0]["ok"])
