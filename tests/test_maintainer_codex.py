from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.maintainer import scan, store
from scripts import maintainer_runner as runner


class FakeFeed:
    def __init__(self):
        self.items = []

    def add(self, kind, text):
        self.items.append((kind, text))


class MaintainerCodexTests(unittest.TestCase):
    def test_child_environment_removes_provider_credentials(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret", "SAFE_VALUE": "kept"
        }, clear=True):
            env = runner.child_env()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertEqual(env["SAFE_VALUE"], "kept")

    def test_consumes_codex_jsonl_summary_and_turn(self):
        feed, result = FakeFeed(), {"turns": 0, "summary": "", "error": None}
        runner._consume(json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Implemented the fix."},
        }), feed, result)
        runner._consume(json.dumps({"type": "turn.completed", "usage": {}}), feed, result)
        self.assertEqual(result["summary"], "Implemented the fix.")
        self.assertEqual(result["turns"], 1)
        self.assertIn(("text", "Implemented the fix."), feed.items)


class NightlyAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_repository_is_still_audited(self):
        old_db, old_ready = store._DB, store._READY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store._DB = Path(tmp) / "maintainer.db"
                store._READY = False
                c = store.conn()
                store.mark_scanned(c, "Example", "same-sha", 0)
                c.close()

                candidate = {
                    "title": "Add parser edge-case coverage",
                    "brief": "Cover the supplied parser branch.",
                    "rationale": "The sampled parser has an untested edge case.",
                    "acceptance": "The focused test passes.",
                    "files": ["src/parser.py", "tests/test_parser.py"],
                    "risk": "low",
                }
                repo = {"name": "Example", "root": "/repo", "base": "main"}
                with (patch.object(scan.repos, "all_repos", return_value=[repo]),
                      patch.object(scan.repos, "head_sha", return_value="same-sha"),
                      patch.object(scan.repos, "digest", return_value={"repo": "Example"})
                      as digest,
                      patch.object(scan, "_propose", return_value=[candidate]) as propose):
                    result = await scan.scan_repos()

                self.assertEqual(result["scanned"], ["Example"])
                self.assertEqual(result["proposed"], 1)
                digest.assert_called_once()
                propose.assert_awaited_once()
        finally:
            store._DB, store._READY = old_db, old_ready


if __name__ == "__main__":
    unittest.main()
