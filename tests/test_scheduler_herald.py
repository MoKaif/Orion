from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from orion.core.scheduler import ScheduledJob, Scheduler
from plugins.herald import mailer, store, digest


class SchedulerJobTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_queued_result_is_not_a_failure_but_config_error_is(self):
        for result, expected in [
            ({"ok": False, "outcome": "queued", "reason": "daily cap reached (4/4 in the last 24h)"}, True),
            ({"ok": False, "outcome": "queued", "reason": "quiet hours"}, True),
            ({"ok": False, "outcome": "queued", "reason": "No sender credentials"}, False),
            ({"ok": False, "outcome": "failed", "reason": "SMTP offline"}, False),
        ]:
            scheduler = Scheduler()
            with patch.object(scheduler, "_record_run") as record:
                await scheduler._execute(ScheduledJob("mail", "* * * * *", lambda: result))
            self.assertIs(record.call_args.args[1], expected)

    def test_legacy_policy_deferrals_are_corrected_on_history_load(self):
        result = {"ok": False, "outcome": "queued", "reason": "quiet hours"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.json"
            path.write_text(json.dumps({"mail": [{"ok": False, "result": str(result)}]}))
            with patch("orion.core.scheduler._RUNS_FILE", path):
                run = Scheduler().history("mail")[0]
        self.assertTrue(run["ok"])
        self.assertEqual(run["status"], "deferred")

    async def test_returned_failure_is_recorded_as_failure(self):
        for result in ({"ok": False, "error": "offline"},
                       {"grammar": {"ok": False, "error": "timeout"}}):
            scheduler = Scheduler()
            job = ScheduledJob("failed", "0 0 * * *", lambda: result)
            with patch.object(scheduler, "_record_run") as record:
                await scheduler._execute(job)
            self.assertIs(record.call_args.args[1], False)

    async def test_empty_success_and_deferred_work_are_not_failures(self):
        for result in ({"ok": True, "deferred": 3}, [], None):
            scheduler = Scheduler()
            job = ScheduledJob("success", "0 0 * * *", lambda: result)
            with patch.object(scheduler, "_record_run") as record:
                await scheduler._execute(job)
            self.assertIs(record.call_args.args[1], True)

    async def test_sync_job_result_is_not_awaited(self):
        scheduler = Scheduler()
        job = ScheduledJob("sync", "0 0 * * *", lambda: {"created": True})

        with patch.object(scheduler, "_record_run"):
            result = await scheduler._execute(job)

        self.assertEqual(result, {"created": True})
        self.assertEqual(job.last_result, {"created": True})

    async def test_async_job_is_still_awaited(self):
        async def run():
            return {"awaited": True}

        scheduler = Scheduler()
        job = ScheduledJob("async", "0 0 * * *", run)

        with patch.object(scheduler, "_record_run"):
            result = await scheduler._execute(job)

        self.assertEqual(result, {"awaited": True})


class HeraldIdentityTests(unittest.TestCase):
    def test_partial_dedicated_sender_configuration_falls_back_to_owner(self):
        env = {
            "GMAIL_ADDRESS": "owner@example.com",
            "GMAIL_APP_PASSWORD": "owner-password",
            "GMAIL_SENDER_APP_PASSWORD": "herald-password",
        }
        with (patch.dict(os.environ, env, clear=True),
              patch.object(mailer, "settings", return_value={"enabled": True})):
            status = mailer.status()
            sender = mailer.sender_address()

        self.assertTrue(status["ok"])
        self.assertFalse(status["dedicated_sender"])
        self.assertIn("using GMAIL_ADDRESS", status["reason"])
        self.assertEqual(sender, "owner@example.com")


class HeraldQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_ready = store._DB, store._READY
        store._DB, store._READY = Path(self.tmp.name) / "herald.db", False

    def tearDown(self):
        store._DB, store._READY = self.old_db, self.old_ready
        self.tmp.cleanup()

    def test_automatic_queue_coalesces_without_touching_held_or_manual_mail(self):
        c = store.conn()
        try:
            first = store.queue(c, "alert", "owner@example.com", "Old", "html", "old")
            second = store.queue(c, "alert", "owner@example.com", "Latest", "html", "new")
            manual = store.queue(c, "manual", "owner@example.com", "Manual", "html", "manual")
            held = store.queue(c, "alert", "other@example.com", "Held", "html", "held", status="held")
            self.assertEqual(first, second)
            self.assertEqual(store.get(c, first)["text"], "new")
            store.compact_queue(c)
            self.assertEqual(store.get(c, manual)["status"], "queued")
            self.assertEqual(store.get(c, held)["status"], "held")
        finally:
            c.close()

    def test_old_automatic_messages_are_cancelled_with_bodies_preserved(self):
        c = store.conn()
        try:
            mid = store.queue(c, "briefing", "owner@example.com", "Old", "html", "audit body")
            c.execute("UPDATE outbox SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (mid,))
            c.commit()
            self.assertEqual(store.compact_queue(c), 1)
            row = store.get(c, mid)
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(row["text"], "audit body")
        finally:
            c.close()

    async def test_repeated_alert_checks_do_not_alert_on_herald_or_grow_queue(self):
        from orion.core.scheduler import scheduler
        jobs = [SimpleNamespace(name="herald_alerts", agent="herald", display_label="Watch"),
                SimpleNamespace(name="calendar", agent="chronos", display_label="Calendar")]
        history = [{"ok": False, "result": "Calendar authorization required", "at": "2099-01-01"}]
        with (patch.object(scheduler, "jobs", return_value=jobs),
              patch.object(scheduler, "history", return_value=history),
              patch.object(mailer, "recipient", return_value="owner@example.com"),
              patch.object(mailer, "is_self", return_value=True),
              patch.object(mailer, "may_send", return_value=(False, "daily cap reached (4/4 in the last 24h)")),
              patch.object(digest, "_spend", return_value={"cost_usd": 0}),
              patch("orion.core.reports.alerts", return_value=[]),
              patch("orion.core.providers.router.usage", return_value={}),
              patch.object(mailer, "_send_row", new_callable=AsyncMock) as send):
            first = await digest.watch_alerts()
            second = await digest.watch_alerts()
        self.assertTrue(first["ok"])
        self.assertTrue(second["deferred"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["alerts"], 1)
        send.assert_not_awaited()
        c = store.conn()
        try:
            self.assertNotIn("Watch failed", store.get(c, first["id"])["text"])
        finally:
            c.close()

    async def test_briefing_counts_repeated_calendar_errors_as_one_failed_job(self):
        runs = [{"job": "calendar", "label": "Calendar", "ok": False, "result": "authorization"}] * 21
        with (patch.object(digest, "_runs_since", return_value=runs),
              patch.object(digest, "_inbox", return_value=[]),
              patch.object(digest, "_stats", return_value={}),
              patch.object(digest, "_spend", return_value={"cost_usd": 0}),
              patch.object(digest, "_plugin_facts", return_value={}),
              patch.object(digest, "_plugin_sections", return_value=[]),
              patch.object(digest, "_lede", new_callable=AsyncMock, return_value=""),
              patch.object(mailer, "deliver", new_callable=AsyncMock, return_value={"ok": True}) as send):
            result = await digest.morning_briefing()
        self.assertEqual(result["failures"], 1)
        self.assertIn("1 job failed", send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
