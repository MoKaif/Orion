from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.model_scout import engine, store


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, *, posts=None, gets=None, **_kwargs):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json):
        self.requests.append((url, json))
        return _Response(self.posts.pop(0))

    async def get(self, url):
        self.requests.append((url, None))
        return _Response(self.gets.pop(0))


class ModelScoutStoreTests(unittest.TestCase):
    def setUp(self):
        self.old_db, self.old_ready = store._DB, store._READY
        self.tmp = tempfile.TemporaryDirectory()
        store._DB, store._READY = Path(self.tmp.name) / "model_scout.db", False

    def tearDown(self):
        store._DB, store._READY = self.old_db, self.old_ready
        self.tmp.cleanup()

    def test_research_metadata_is_separate_from_benchmark_history(self):
        c = store.conn()
        try:
            store.set_metadata(c, "latest_research", {"candidates": [{"model": "tiny"}]})
            self.assertEqual(store.metadata(c, "latest_research")["value"]["candidates"][0]["model"], "tiny")
            self.assertIsNone(store.latest_run(c))
        finally:
            c.close()

    def test_recommendations_are_idempotent(self):
        c = store.conn()
        try:
            args = dict(fingerprint="trial:x", kind="trial", candidate="x", title="Try x",
                        body="Evidence", effect="No automatic change", evidence={"score": .8})
            self.assertIsNotNone(store.propose(c, **args))
            self.assertIsNone(store.propose(c, **args))
            self.assertEqual(len(store.recommendations(c)), 1)
        finally:
            c.close()


class ModelScoutEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_ollama_stays_loaded_until_final_case(self):
        fake = _Client(posts=[
            {"message": {"content": "reflex"}},
            {"message": {"content": "reasoning"}},
        ])
        cases = [("mode", "calendar", "reflex"), ("mode", "compare", "reasoning")]
        with patch.object(engine.httpx, "AsyncClient", return_value=fake):
            result = await engine._ollama("test:1b", cases)
        self.assertEqual(result["accuracy"], 1)
        self.assertEqual([request[1]["keep_alive"] for request in fake.requests], ["5m", 0])

    async def test_laya_answers_are_scored_without_generative_parsing(self):
        fake = _Client(posts=[
            {"answers": {"commitment": {"choice": "event", "answer_confidence": .83}}}
        ])
        with patch.object(engine.httpx, "AsyncClient", return_value=fake):
            result = await engine._laya([("commitment", "Dentist tomorrow", "event")])
        self.assertTrue(result["available"])
        self.assertEqual(result["accuracy"], 1)
        self.assertEqual(result["details"][0]["confidence"], .83)

    async def test_switch_requires_candidate_to_still_be_installed(self):
        old_db, old_ready = store._DB, store._READY
        with tempfile.TemporaryDirectory() as tmp:
            store._DB, store._READY = Path(tmp) / "model_scout.db", False
            c = store.conn()
            try:
                rid = store.propose(c, fingerprint="switch:a:b", kind="switch", candidate="b:1b",
                                    title="Switch", body="Better", effect="Changes local config",
                                    evidence={})
            finally:
                c.close()
            try:
                with patch.object(engine, "_installed", AsyncMock(return_value=set())), \
                        patch.object(engine.config, "update_local") as update:
                    result = await engine.resolve(rid, "accept")
                self.assertFalse(result["ok"])
                update.assert_not_called()
            finally:
                store._DB, store._READY = old_db, old_ready


if __name__ == "__main__":
    unittest.main()
