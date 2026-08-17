from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plugins.curator import grammar, interview, notes, store


class GrammarSafetyTests(unittest.TestCase):
    NOTE = """---
type: journal
aliases: [\"Example\"]
---
# Heading
- I has met [[Naufil]] at [JP North](https://example.com) #people.
`literal`
```python
print("untouched")
```
"""

    def test_allows_prose_only_correction(self):
        corrected = self.NOTE.replace("I has met", "I met")
        self.assertTrue(grammar.preserves_structure(self.NOTE, corrected))

    def test_rejects_removed_wikilink(self):
        corrected = self.NOTE.replace("[[Naufil]]", "Naufil")
        self.assertFalse(grammar.preserves_structure(self.NOTE, corrected))

    def test_rejects_wikilink_moved_to_another_line(self):
        corrected = self.NOTE.replace("[[Naufil]]", "Naufil").replace(
            "`literal`", "[[Naufil]] `literal`")
        self.assertFalse(grammar.preserves_structure(self.NOTE, corrected))

    def test_rejects_embed_changed_to_plain_link(self):
        note = self.NOTE.replace("[[Naufil]]", "![[Naufil]]")
        self.assertFalse(grammar.preserves_structure(note, note.replace("![[", "[[")))

    def test_rejects_frontmatter_heading_and_list_changes(self):
        self.assertFalse(grammar.preserves_structure(
            self.NOTE, self.NOTE.replace("type: journal", "type: note")))
        self.assertFalse(grammar.preserves_structure(
            self.NOTE, self.NOTE.replace("# Heading", "# Better heading")))
        self.assertFalse(grammar.preserves_structure(
            self.NOTE, self.NOTE.replace("- I has", "* I has")))

    def test_generated_hub_is_not_editable(self):
        hub = "---\ntype: person\ncurator_entity_id: 42\n---\n\n# Naufil\n"
        self.assertEqual(notes.classify(hub), "hub")
        self.assertFalse(notes.editable(notes.classify(hub)))


class BacklinkCheckpointTests(unittest.TestCase):
    def test_old_checkpoint_version_is_invalidated_once(self):
        old_db, old_ready = store._DB, store._READY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store._DB = Path(tmp) / "curator.db"
                store._READY = False
                c = store.conn()
                store.mark_note(c, "Journal/day.md", sha="note", linked_sha="note")
                c.execute("UPDATE meta SET value='1' WHERE key='backlink_checkpoint_version'")
                c.commit()
                c.close()

                store._READY = False
                c = store.conn()
                linked = c.execute(
                    "SELECT linked_sha FROM notes WHERE path='Journal/day.md'").fetchone()[0]
                version = c.execute(
                    "SELECT value FROM meta WHERE key='backlink_checkpoint_version'").fetchone()[0]
                c.close()
                self.assertIsNone(linked)
                self.assertEqual(version, store._BACKLINK_CHECKPOINT_VERSION)
        finally:
            store._DB, store._READY = old_db, old_ready


class MemoryInterviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_ready = store._DB, store._READY
        store._DB = Path(self.tmp.name) / "data" / "curator.db"
        store._READY = False
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        self.vault_patch = mock.patch.object(notes, "vault", return_value=self.vault)
        self.vault_patch.start()

    def tearDown(self):
        self.vault_patch.stop()
        store._DB, store._READY = self.old_db, self.old_ready
        self.tmp.cleanup()

    def test_only_one_prompt_waits_and_replacement_rotates_subject(self):
        first = interview.ensure_prompt()
        same = interview.ensure_prompt()
        second = interview.another_subject()

        self.assertEqual(first["id"], same["id"])
        self.assertNotEqual(first["prompt_key"], second["prompt_key"])
        c = store.conn()
        try:
            self.assertEqual(interview.stats(c)["open"], 1)
            self.assertEqual(interview.stats(c)["skipped"], 1)
        finally:
            c.close()

    def test_prompt_bank_is_varied_and_scene_shaped(self):
        prompts = interview._PROMPTS
        self.assertGreaterEqual(len(prompts), 30)
        self.assertEqual(len({p.key for p in prompts}), len(prompts))
        self.assertEqual(len({p.question for p in prompts}), len(prompts))
        self.assertTrue(all(p.question.endswith("?") for p in prompts))
        self.assertTrue(all("tell me about" not in p.question.lower() for p in prompts))

    def test_answer_is_saved_verbatim_and_protected_from_editing(self):
        prompt = interview.ensure_prompt()
        answer = "I remember the red staircase.\n\nWe used to sit there after school—usually quietly."

        result = interview.save_answer(prompt["id"], answer)

        self.assertTrue(result["ok"])
        saved = (self.vault / result["note_path"]).read_text(encoding="utf-8")
        self.assertIn(f"## My memory\n\n{answer}\n", saved)
        self.assertIn("preserve_voice: true", saved)
        self.assertEqual(notes.classify(saved), "raw_memory")
        self.assertFalse(notes.editable(notes.classify(saved)))
        self.assertTrue(notes.mineable(notes.classify(saved)))

    def test_existing_note_is_never_overwritten(self):
        prompt = interview.ensure_prompt()
        first = interview.save_answer(prompt["id"], "The first version.")
        again = interview.save_answer(prompt["id"], "A replacement that must not be written.")

        self.assertEqual(again["outcome"], "already_saved")
        saved = (self.vault / first["note_path"]).read_text(encoding="utf-8")
        self.assertIn("The first version.", saved)
        self.assertNotIn("A replacement", saved)

    def test_i_do_not_remember_skips_without_creating_a_note(self):
        prompt = interview.ensure_prompt()
        result = interview.save_answer(prompt["id"], "I don't remember.")

        self.assertEqual(result["outcome"], "skipped")
        self.assertEqual(list(self.vault.rglob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
