from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from orion.core.scheduler import ScheduledJob, Scheduler
from plugins.herald import mailer


class SchedulerJobTypeTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
