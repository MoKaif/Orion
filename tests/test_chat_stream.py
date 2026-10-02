from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from orion.core.api.app import ChatIn, chat_stream
from orion.core.gate import LocalModelGate


def event(chunk: str | bytes) -> dict:
    if isinstance(chunk, bytes):
        chunk = chunk.decode()
    return json.loads(chunk.removeprefix("data: ").strip())


class ChatStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_reaches_client_before_first_token(self):
        async def turn(message, session_id, on_event):
            on_event({"type": "context", "mode": "reflex", "specialist": "generalist"})
            await asyncio.sleep(0)
            yield "hello"

        with patch("orion.core.api.app.orchestrator.handle_turn", side_effect=turn):
            response = await chat_stream(ChatIn(message="hi", session_id=7))
            frames = [event(chunk) async for chunk in response.body_iterator]

        self.assertEqual([frame["type"] for frame in frames],
                         ["start", "context", "token", "done"])
        self.assertEqual(frames[2]["text"], "hello")


class BackgroundGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_callable_is_not_created_until_foreground_is_idle(self):
        gate = LocalModelGate()
        created = False

        async def work():
            return None

        def factory():
            nonlocal created
            created = True
            return work()

        async with gate.foreground():
            task = asyncio.create_task(gate.run_background(factory))
            await asyncio.sleep(0)
            self.assertFalse(created)
            task.cancel()
            self.assertFalse(await task)

        self.assertTrue(await gate.run_background(factory))
        self.assertTrue(created)


if __name__ == "__main__":
    unittest.main()
