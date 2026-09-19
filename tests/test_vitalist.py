from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

from plugins.health import analysis, inference, source, store


def payload() -> dict:
    return {
        "front_page": {
            "window_start": "2026-08-21T00:00:00Z",
            "last_sync": "2026-09-19T01:00:00Z",
            "metrics": [
                {"metric_name": "step_count", "label": "Steps", "unit": "count",
                 "multiplier": 1, "series": [5000.0] * 28 + [7000.0, None]},
                {"metric_name": "distance_walking_running", "label": "Walking + Running",
                 "unit": "km", "multiplier": .001,
                 "series": [4000.0] * 28 + [5600.0, None]},
                {"metric_name": "heart_rate", "label": "Heart Rate", "unit": "bpm",
                 "multiplier": 1, "series": [None] * 28 + [72.0, None]},
            ],
        },
        "sleep": {"sessions": None, "stages": None},
        "workouts": [],
    }


class VitalistAnalysisTests(unittest.TestCase):
    def test_daily_facts_apply_perseus_multiplier_and_handle_null_sleep(self):
        facts = analysis.build(payload(), "daily", today=date(2026, 9, 19))
        metrics = {m["name"]: m for m in facts["metrics"]}
        self.assertEqual(metrics["step_count"]["current"], 7000)
        self.assertEqual(metrics["distance_walking_running"]["current"], 5.6)
        self.assertEqual(facts["sleep"]["nights"], 0)
        self.assertNotIn("resting_heart_rate", metrics)

    def test_weekly_comparison_uses_complete_days_only(self):
        facts = analysis.build(payload(), "weekly", today=date(2026, 9, 19))
        steps = next(m for m in facts["metrics"] if m["name"] == "step_count")
        self.assertEqual(steps["current_days"], 7)
        self.assertGreater(steps["current"], steps["baseline"])

    def test_llm_parser_rejects_unstructured_health_claim(self):
        self.assertIsNone(inference._parse("You may have a heart condition."))
        parsed = inference._parse('{"summary":"Activity was steady.","insights":["Sleep is missing."]}')
        self.assertEqual(parsed["generated_by"], "llm")


class VitalistSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_normalizes_null_sleep_lists(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/metrics/latest"):
                return httpx.Response(200, json={"metrics": [], "window_start": "2026-08-01"})
            if request.url.path.endswith("/sleep"):
                return httpx.Response(200, json={"sessions": None, "stages": None})
            return httpx.Response(200, json=[])

        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with (patch.object(source, "settings", return_value={
                "timezone": "Asia/Kolkata", "analysis": {"history_days": 30},
                "perseus": {"base_url": "http://perseus.test", "timeout_seconds": 5}}),
              patch.object(source.httpx, "AsyncClient",
                           side_effect=lambda *a, **kw: real_client(*a, transport=transport, **kw))):
            result = await source.collect(today=date(2026, 9, 19))
        self.assertEqual(result["sleep"], {"sessions": [], "stages": []})


class VitalistStoreTests(unittest.TestCase):
    def test_report_is_idempotent_for_period(self):
        old_db, old_ready = store._DB, store._READY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store._DB, store._READY = Path(tmp) / "vitalist.db", False
                facts = analysis.build(payload(), "daily", today=date(2026, 9, 19))
                c = store.conn()
                first = store.save(c, facts, {"summary": "One", "insights": [],
                                              "generated_by": "deterministic"})
                second = store.save(c, facts, {"summary": "Two", "insights": [],
                                               "generated_by": "llm"})
                report = store.latest(c, "daily")
                self.assertEqual(first, second)
                self.assertEqual(report["prose"]["summary"], "Two")
                c.close()
        finally:
            store._DB, store._READY = old_db, old_ready


if __name__ == "__main__":
    unittest.main()
