from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.curator import grammar, notes, store


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


if __name__ == "__main__":
    unittest.main()
