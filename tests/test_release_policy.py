from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release


class ReleasePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "orion").mkdir()
        self.init = self.root / "orion" / "__init__.py"
        self.notes = self.root / "CHANGELOG.md"
        self.init.write_text('__version__ = "0.15.0"\n')
        self.notes.write_text("# Changelog\n\n<!-- releases -->\n")
        for args in (["init", "-b", "main"], ["config", "user.name", "Fixture"],
                     ["config", "user.email", "fixture@example.invalid"],
                     ["add", "."], ["commit", "-m", "Fixture"]):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.paths = patch.multiple(release, ROOT=self.root, INIT=self.init, CHANGELOG=self.notes)
        self.paths.start()
        self.head = release.git("rev-parse", "HEAD")

    def tearDown(self):
        self.paths.stop()
        self.tmp.cleanup()

    def invoke(self, *args):
        with patch.object(sys, "argv", ["release.py", "-m", "Fixture release", *args]):
            return release.main()

    def test_default_preparation_never_commits_tags_pushes_or_stages_unrelated_files(self):
        (self.root / "private.txt").write_text("unrelated local work")
        self.invoke("minor")
        self.assertEqual(release.read_version(), "0.16.0")
        self.assertIn("0.16.0", self.notes.read_text())
        self.assertEqual(release.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(release.git("tag", "--list"), "")
        self.assertEqual(release.git("diff", "--cached", "--name-only"), "")
        self.assertEqual((self.root / "private.txt").read_text(), "unrelated local work")

    def test_unreleased_notes_preserve_version_and_history(self):
        self.invoke("--unreleased", "--allow-empty")
        self.assertEqual(release.read_version(), "0.15.0")
        self.assertIn("[Unreleased]", self.notes.read_text())
        self.assertEqual(release.git("rev-parse", "HEAD"), self.head)

    def test_remote_mutation_flags_require_explicit_prerequisites(self):
        for flags in (("--push",), ("--tag",), ("--prepare-only", "--commit")):
            with self.assertRaises(release.ReleaseError):
                self.invoke(*flags)
        self.assertEqual(release.read_version(), "0.15.0")

    def test_metadata_commit_refuses_unrelated_user_changes(self):
        (self.root / "work.py").write_text("user work")
        with self.assertRaisesRegex(release.ReleaseError, "implementation changes first"):
            self.invoke("--commit")
        self.assertEqual(release.read_version(), "0.15.0")
        self.assertEqual(release.git("rev-parse", "HEAD"), self.head)

    def test_missing_changelog_marker_does_not_partially_bump_version(self):
        self.notes.write_text("No marker")
        with self.assertRaisesRegex(release.ReleaseError, "insertion marker"):
            self.invoke()
        self.assertEqual(release.read_version(), "0.15.0")

    def test_commit_and_local_tag_are_explicit_and_never_require_a_remote(self):
        self.invoke("minor", "--commit", "--tag", "--allow-empty")
        self.assertEqual(release.git("tag", "--list"), "v0.16.0")
        self.assertNotEqual(release.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(release.git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
