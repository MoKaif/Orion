from __future__ import annotations

import tempfile
import unittest
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from plugins.finance import analytics, engine, inference, models, source, store


def transaction(i: int, when: date, amount: float, destination: str = "Expenses:Dining",
                *, source: str = "Assets:Banking:HDFCBank", mapped: bool = True) -> dict:
    return {
        "id": i, "txnDate": when.isoformat(), "descriptionClean": "Example Merchant 123456",
        "amount": amount, "accountFrom": source, "accountTo": destination,
        "category": destination.split(":")[-1], "mapped": mapped,
        "createdAt": when.isoformat() + "T12:00:00+00:00",
    }


CFG = {
    "analysis": {"history_days": 400, "baseline_weeks": 8, "minimum_baseline_weeks": 4,
                 "minimum_category_spend_inr": 1000, "minimum_change_inr": 750,
                 "spike_ratio": 1.35, "critical_spike_ratio": 1.75,
                 "unusual_transaction_inr": 2500, "stale_after_hours": 99999,
                 "unknown_flow_ratio": .05, "max_insights": 12},
    "ml": {"enabled": False},
}


class TreasurerSemanticsTests(unittest.TestCase):
    def test_amount_sign_does_not_change_direction_and_investing_is_separate(self):
        anchor = date(2026, 8, 16)
        rows = [
            transaction(1, anchor, -500),
            transaction(2, anchor, 300, source="Expenses:Dining", destination="Assets:Banking:HDFCBank"),
            transaction(3, anchor, -2000, destination="Assets:Investment:MutualFunds"),
        ]
        snap, _ = analytics.snapshot(rows, CFG, anchor=anchor)
        totals = snap["periods"]["last_7d"]
        self.assertEqual(totals["spending"], 200)
        self.assertEqual(totals["refunds"], 300)
        self.assertEqual(totals["investing"], 2000)

    def test_personal_baseline_detects_overall_and_category_spike(self):
        anchor = date(2026, 8, 16)
        rows = []
        for i in range(70):
            amount = 1000 if i < 7 else 100
            rows.append(transaction(i + 1, anchor - timedelta(days=i), amount))
        snap, classified = analytics.snapshot(rows, CFG, anchor=anchor)
        found, forecast = analytics.detect(snap, classified, CFG)
        self.assertIsNone(forecast)
        self.assertIn("spending_spike", {i["kind"] for i in found})
        self.assertIn("category_spike", {i["kind"] for i in found})
        self.assertTrue(all("evidence" in i and "confidence" in i for i in found))
        # Starlette deliberately rejects NaN/Infinity; insight evidence is an API contract.
        json.dumps(found, allow_nan=False)

    def test_llm_parser_rejects_unstructured_text(self):
        self.assertEqual(inference._parse("I think you spent more."), {})
        parsed = inference._parse('{"insights":[{"fingerprint":"x","explanation":"Evidence says so"}]}')
        self.assertEqual(parsed["x"]["explanation"], "Evidence says so")

    def test_ml_has_honest_dependency_fallback(self):
        with patch.object(models, "_sklearn", return_value=None):
            self.assertIsNone(models.weekly_forecast({}, date(2026, 8, 16)))
            self.assertEqual(models.unusual_transactions([], minimum_amount=1000), [])


class TreasurerStoreTests(unittest.TestCase):
    def test_insight_lifecycle_and_user_feedback_are_durable(self):
        old_db, old_ready = store._DB, store._READY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store._DB = Path(tmp) / "treasurer.db"
                store._READY = False
                c = store.conn()
                finding = {"fingerprint": "category_spike:dining", "kind": "category_spike",
                           "scope": "Dining", "title": "Dining is high", "detail": "Evidence",
                           "evidence": {"observed": 4000}, "confidence": .8,
                           "severity": "attention", "method": "median_mad",
                           "llm": {"explanation": "Frequency increased."}}
                ids = store.upsert_insights(c, [finding])
                self.assertEqual(len(ids), 1)
                self.assertEqual(store.insight(c, ids[0])["review_state"], "pending")
                self.assertTrue(store.review(c, ids[0], "expected"))
                without_llm = {k: v for k, v in finding.items() if k != "llm"}
                store.upsert_insights(c, [without_llm])
                self.assertEqual(store.insight(c, ids[0])["llm"]["explanation"], "Frequency increased.")
                store.upsert_insights(c, [])
                item = store.insight(c, ids[0])
                self.assertEqual(item["review_state"], "expected")
                self.assertEqual(item["status"], "resolved")
                c.close()
        finally:
            store._DB, store._READY = old_db, old_ready


class TreasurerEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_analysis_persists_snapshot_model_and_evidence(self):
        old_db, old_ready = store._DB, store._READY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store._DB = Path(tmp) / "treasurer.db"
                store._READY = False
                anchor = date.today()
                rows = [transaction(i + 1, anchor - timedelta(days=i), 900 if i < 7 else 100)
                        for i in range(100)]
                cfg = {**CFG, "llm": {"enabled": False}}
                with (patch.object(source, "transactions", return_value=rows),
                      patch.object(engine, "settings", return_value=cfg)):
                    result = await engine.analyze(use_llm=False)
                self.assertTrue(result["ok"])
                c = store.conn()
                self.assertIsNotNone(store.latest_snapshot(c))
                self.assertIsNotNone(store.latest_model_run(c))
                self.assertGreater(len(store.insights(c)), 0)
                c.close()
        finally:
            store._DB, store._READY = old_db, old_ready


if __name__ == "__main__":
    unittest.main()
