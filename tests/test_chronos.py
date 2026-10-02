from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.chronos import calendar, inference, store


class ChronosInferenceTests(unittest.TestCase):
    def test_rejects_past_and_caps_model_confidence(self):
        self.assertIsNone(inference._parse(
            '{"title":"Old","start_at":"2026-09-01","confidence":1}',
            today=date(2026, 10, 2), horizon=31))
        item = inference._parse(
            '{"title":"Review","start_at":"2026-10-12T10:00:00+05:30","confidence":1}',
            today=date(2026, 10, 2), horizon=31)
        self.assertEqual(item["confidence"], .95)

    def test_prefilter_ignores_mail_without_a_time_signal(self):
        self.assertFalse(inference.likely_relevant({"subject": "Welcome", "text": "Thanks for joining."}))
        self.assertTrue(inference.likely_relevant({"subject": "Meeting next Tuesday", "text": ""}))


class ChronosInferenceResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_mail_extraction_caps_cpu_and_unloads_model(self):
        provider = MagicMock()
        provider.is_available = AsyncMock(return_value=True)
        provider.complete = AsyncMock(return_value="[]")
        message = {"fingerprint": "mail-1", "from": "sender@example.com",
                   "subject": "Meeting tomorrow", "date": "today", "text": "At 10 am"}

        with patch.object(inference.router, "get", return_value=provider):
            self.assertEqual(await inference.extract_many([message]), [])

        kwargs = provider.complete.await_args.kwargs
        self.assertEqual(kwargs["options"]["num_thread"], 1)
        self.assertEqual(kwargs["options"]["num_predict"], 384)
        self.assertEqual(kwargs["keep_alive"], 0)

    async def test_timeout_explicitly_unloads_model(self):
        provider = MagicMock()
        provider.is_available = AsyncMock(return_value=True)
        provider.complete = AsyncMock(side_effect=[TimeoutError(), ""])
        message = {"fingerprint": "mail-1", "from": "sender@example.com",
                   "subject": "Meeting tomorrow", "date": "today", "text": "At 10 am"}

        with patch.object(inference.router, "get", return_value=provider):
            self.assertIsNone(await inference.extract_many([message]))

        self.assertEqual(provider.complete.await_count, 2)
        self.assertEqual(provider.complete.await_args.kwargs["keep_alive"], 0)


class ChronosStoreTests(unittest.TestCase):
    def setUp(self):
        self.old_db, self.old_ready = store._DB, store._READY
        self.tmp = tempfile.TemporaryDirectory()
        store._DB, store._READY = Path(self.tmp.name) / "chronos.db", False

    def tearDown(self):
        store._DB, store._READY = self.old_db, self.old_ready
        self.tmp.cleanup()

    def test_mail_proposal_is_idempotent(self):
        item = {"source_kind": "mail", "source_ref": "abc", "title": "Appointment",
                "start_at": "2099-01-02T10:00:00+05:30", "confidence": .8}
        c = store.conn()
        try:
            self.assertIsNotNone(store.add_proposal(c, item))
            self.assertIsNone(store.add_proposal(c, item))
            self.assertEqual(len(store.proposals(c)), 1)
        finally:
            c.close()


class ChronosCalendarTests(unittest.TestCase):
    def test_approved_event_targets_primary_without_guest_notifications(self):
        execute = MagicMock(return_value={"id": "event-1", "htmlLink": "https://calendar.test/1"})
        insert = MagicMock(return_value=MagicMock(execute=execute))
        service = MagicMock()
        service.events.return_value.insert = insert
        with (patch.object(calendar, "_service", return_value=service),
              patch.object(calendar, "settings", return_value={
                  "timezone": "Asia/Kolkata", "calendar": {"id": "primary"}})):
            result = calendar.create_event({"title": "Review", "start_at": "2099-01-02",
                                            "description": "Found in mail"})
        self.assertEqual(result["id"], "event-1")
        kwargs = insert.call_args.kwargs
        self.assertEqual(kwargs["calendarId"], "primary")
        self.assertEqual(kwargs["sendUpdates"], "none")
        self.assertEqual(kwargs["body"]["visibility"], "private")


if __name__ == "__main__":
    unittest.main()
