from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.guardian import service


class GuardianTests(unittest.TestCase):
    def test_backup_is_consistent_and_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "config").mkdir()
            db = sqlite3.connect(root / "data" / "orion.db")
            db.execute("CREATE TABLE facts (value TEXT)")
            db.execute("INSERT INTO facts VALUES ('safe')")
            db.commit()
            db.close()
            (root / "config" / "settings.json").write_text('{"enabled": true}\n')
            (root / "config" / "secrets.json").write_text('{"TOKEN": "never-copy"}\n')

            with (patch.object(service.config, "root", return_value=root),
                  patch.object(service, "settings", return_value={"enabled": True, "retention": 2})):
                result = service.create_backup()
                snapshots = service.backups()

            self.assertTrue(result["ok"])
            snapshot = root / "data" / "backups" / "guardian" / snapshots[0]["name"]
            self.assertTrue((snapshot / "data" / "orion.db").is_file())
            self.assertTrue((snapshot / "config" / "settings.json").is_file())
            self.assertFalse((snapshot / "config" / "secrets.json").exists())
            copied = sqlite3.connect(snapshot / "data" / "orion.db")
            self.assertEqual(copied.execute("SELECT value FROM facts").fetchone()[0], "safe")
            copied.close()

    def test_audit_reports_missing_backup_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text(json.dumps({"ok": True}))
            with (patch.object(service.config, "root", return_value=root),
                  patch.object(service, "settings", return_value={"minimum_free_gb": 0,
                                                                   "stale_after_hours": 36})):
                result = service.audit()
            self.assertFalse(result["ok"])
            self.assertIn("no Guardian backup exists yet", result["problems"])


if __name__ == "__main__":
    unittest.main()
