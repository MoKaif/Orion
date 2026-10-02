from pathlib import Path

from orion.core.world_model.store import WorldModel
from orion.core.world_model.extract import _parse
from plugins.knowledge.ingest import _chunks


def test_keyword_recall_ignores_conversational_scaffolding(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("orion.core.world_model.store.vectors.is_available", lambda: False)
    monkeypatch.setattr("orion.core.world_model.store.vectors.add", lambda *args, **kwargs: None)
    wm = WorldModel(tmp_path / "world.db")
    books = wm.upsert_entity("note", "Meditations")
    project = wm.upsert_entity("workspace", "Orion")
    wm.add_knowledge(books, "content", "What I know about dealing with difficult people")
    wm.add_knowledge(project, "summary", "Active personal knowledge operating system project")

    hits = wm.recall("What projects am I working on?", limit=5)

    assert [hit["name"] for hit in hits] == ["Orion"]
    assert hits[0]["source"] is None


def test_recall_diversifies_chunked_notes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("orion.core.world_model.store.vectors.is_available", lambda: False)
    monkeypatch.setattr("orion.core.world_model.store.vectors.add", lambda *args, **kwargs: None)
    wm = WorldModel(tmp_path / "world.db")
    first = wm.upsert_entity("note", "First")
    second = wm.upsert_entity("note", "Second")
    for i in range(4):
        wm.add_knowledge(first, f"content:{i:04d}", f"orion architecture passage {i}")
    wm.add_knowledge(second, "content:0000", "orion architecture alternative")

    hits = wm.recall("orion architecture", limit=5)

    assert sum(hit["name"] == "First" for hit in hits) == 2
    assert any(hit["name"] == "Second" for hit in hits)


def test_overview_prefers_structured_knowledge(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("orion.core.world_model.store.vectors.add", lambda *args, **kwargs: None)
    wm = WorldModel(tmp_path / "world.db")
    note = wm.upsert_entity("note", "Imported book")
    person = wm.upsert_entity("person", "Kaif")
    wm.add_knowledge(note, "content", "A long imported passage")
    wm.add_knowledge(person, "preference", "Uses Obsidian")

    hits = wm.overview(limit=2)

    assert [hit["name"] for hit in hits] == ["Kaif", "Imported book"]


def test_sync_knowledge_removes_stale_chunks(tmp_path: Path, monkeypatch):
    removed: list[str] = []
    monkeypatch.setattr("orion.core.world_model.store.vectors.add", lambda *args, **kwargs: None)
    monkeypatch.setattr("orion.core.world_model.store.vectors.remove", lambda refs, **kwargs: removed.extend(refs))
    wm = WorldModel(tmp_path / "world.db")
    entity = wm.upsert_entity("note", "Long note")
    wm.sync_knowledge(entity, "content", [("content:0000", "old"), ("content:0001", "gone")])
    wm.sync_knowledge(entity, "content", [("content:0000", "new")])

    with wm._connect() as conn:
        rows = conn.execute("SELECT key,value FROM knowledge ORDER BY key").fetchall()
    assert [tuple(row) for row in rows] == [("content:0000", "new")]
    assert removed


def test_markdown_chunks_preserve_heading_context():
    text = "# Project Orion\n\n" + ("architecture details " * 180) + "\n\n## Decision\n\nUse SQLite."
    chunks = _chunks(text, limit=240)

    assert len(chunks) > 2
    assert all(len(chunk) < 520 for chunk in chunks)
    assert any("# Project Orion" in chunk for chunk in chunks[:-1])
    assert "## Decision" in chunks[-1]


def test_chat_extraction_never_silently_auto_accepts():
    candidates = _parse(
        '[{"entity":"user","value":"likes tea","confidence":1.0}]'
    )

    assert candidates[0]["confidence"] == 0.99
