"""Semantic recall: local embeddings (fastembed) + in-process KNN (sqlite-vec).

Deliberately NOT a separate ChromaDB service (odysseus does that) — we cannot spare the RAM
on an 8GB box. Everything stays in the one SQLite file.

Embedding *lanes* (borrowed from odysseus): a vector collection's dimensionality is fixed on
first insert, so vectors from different embedding models live in separate lanes to avoid
corruption if the embedding model ever changes.

Degrades gracefully: if fastembed / sqlite-vec are not installed, or the SQLite build cannot
load extensions, ``is_available()`` returns False and the WorldModel falls back to keyword
recall. The interface never raises.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from orion.core.config import config

log = logging.getLogger("orion.vectors")
# fastembed: knowledge; tools: tool descriptions (kept separate so tool vectors never pollute
# knowledge recall); custom: reserved for a user-configured embedding endpoint.
LANES = ("fastembed", "tools", "custom")


class VectorIndex:
    def __init__(self) -> None:
        cfg = config.section("memory").get("vectors", {})
        self._dim = cfg.get("dim", 384)
        self._model_name = config.provider_cfg("fastembed").get("model", "BAAI/bge-small-en-v1.5")
        self._path: Path | None = None
        self._model = None
        self._checked = False
        self._ok = False

    def bind(self, db_path: Path) -> None:
        """Point the index at the world-model DB (called by WorldModel on init)."""
        self._path = db_path

    # -- availability & lazy setup ----------------------------------------
    def is_available(self) -> bool:
        if not self._checked:
            self._ok = self._setup()
            self._checked = True
        return self._ok

    def _setup(self) -> bool:
        if self._path is None:
            return False
        try:
            import sqlite_vec  # noqa: F401
            from fastembed import TextEmbedding  # noqa: F401
        except Exception as e:  # not installed on this host
            log.info("vector index disabled (deps missing): %s", e)
            return False
        try:
            conn = self._connect()
            for lane in LANES:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_{lane} "
                    f"USING vec0(ref TEXT, embedding float[{self._dim}])"
                )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("vector index disabled (sqlite-vec unavailable): %s", e)
            return False
        return True

    def _connect(self) -> sqlite3.Connection:
        import sqlite_vec
        conn = sqlite3.connect(self._path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _encode(self, text: str) -> list[float]:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self._model_name)
        return list(next(iter(self._model.embed([text]))))

    # -- api (all no-ops when unavailable) --------------------------------
    def add(self, ref: str, text: str, lane: str = "fastembed") -> None:
        if not self.is_available() or not text.strip():
            return
        try:
            import sqlite_vec
            emb = sqlite_vec.serialize_float32(self._encode(text))
            conn = self._connect()
            conn.execute(f"DELETE FROM vec_{lane} WHERE ref=?", (ref,))
            conn.execute(f"INSERT INTO vec_{lane}(ref, embedding) VALUES (?,?)", (ref, emb))
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("vector add failed for %s: %s", ref, e)

    def search(self, query: str, k: int = 10, lane: str = "fastembed") -> list[dict[str, Any]]:
        if not self.is_available() or not query.strip():
            return []
        try:
            import sqlite_vec
            emb = sqlite_vec.serialize_float32(self._encode(query))
            conn = self._connect()
            # `k = ?` (not a bound LIMIT) — newer sqlite-vec doesn't see a parameterised
            # LIMIT as the required knn constraint and rejects the query.
            rows = conn.execute(
                f"SELECT ref, distance FROM vec_{lane} "
                f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (emb, k),
            ).fetchall()
            conn.close()
            # convert distance -> a descending relevance score
            return [{"ref": ref, "score": 1.0 / (1.0 + dist)} for ref, dist in rows]
        except Exception as e:
            log.warning("vector search failed: %s", e)
            return []


vectors = VectorIndex()
