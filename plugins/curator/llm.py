"""Local-only AI primitives for Curator: structured extraction + embeddings.

Both are pinned to the host (Ollama for generation, fastembed for vectors) — vault curation is
bulk background work and must never spend cloud budget, and the human-review gate on every
proposal is what makes small-local-model output safe. Two techniques do the heavy lifting for
quality without a bigger model:

* **Structured decoding** — ``extract`` asks Ollama for ``format=json`` so the model must emit
  parseable JSON, which removes almost all of a 3B model's malformed-output failure mode.
* **Embeddings for resolution** — ``embed`` / ``most_similar`` give entity dedup a semantic
  signal ("mummy" ≈ "mom") that keyword matching can't, reusing the same fastembed model the
  World Model already loads.

Everything degrades: no Ollama ⇒ ``extract`` returns None; no fastembed ⇒ ``embed`` returns
None and callers fall back to normalized-string matching.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from orion.core.config import config

log = logging.getLogger("orion.curator.llm")


# -- structured generation -------------------------------------------------
def _ollama() -> dict:
    return config.provider_cfg("ollama")


async def available() -> bool:
    cfg = _ollama()
    if not cfg.get("enabled", True):
        return False
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(f"{cfg.get('base_url')}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def extract(system: str, user: str) -> Any | None:
    """One JSON-constrained local completion. Returns the parsed object, or None on failure.

    Uses Ollama's ``format=json`` so the model is decoding-constrained to valid JSON; we still
    defensively slice to the outer bracket in case a model wraps it.
    """
    cfg = _ollama()
    payload = {
        "model": cfg.get("model", "qwen2.5:3b"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "format": "json",
        "keep_alive": "30m",
        "options": {"num_ctx": cfg.get("context_window", 4096), "temperature": 0.0},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as c:
            r = await c.post(f"{cfg.get('base_url')}/api/chat", json=payload)
            r.raise_for_status()
            raw = r.json().get("message", {}).get("content", "")
    except Exception as e:
        log.info("curator extract failed: %s", e)
        return None
    return _loads(raw)


def as_list(data: Any, *keys: str) -> list:
    """Coerce a model's JSON into a list of items, tolerating format wobble.

    A 3B model asked for ``{"memories": [...]}`` may instead return the bare array, a
    differently-named wrapper, or a single object — normalize all of them to a list so an
    otherwise-good extraction is never silently dropped.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:                                  # the requested wrapper key
            if isinstance(data.get(k), list):
                return data[k]
        for v in data.values():                         # any list-valued field
            if isinstance(v, list):
                return v
        if any(k in data for k in ("entity", "name", "value")):  # a single candidate
            return [data]
    return []


def _loads(raw: str) -> Any | None:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    for open_c, close_c in (("[", "]"), ("{", "}")):
        s, e = raw.find(open_c), raw.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(raw[s:e + 1])
            except json.JSONDecodeError:
                continue
    return None


# -- embeddings (lazy, optional) -------------------------------------------
_model = None
_checked = False


def _embedder():
    global _model, _checked
    if not _checked:
        _checked = True
        try:
            from fastembed import TextEmbedding
            name = config.provider_cfg("fastembed").get("model", "BAAI/bge-small-en-v1.5")
            _model = TextEmbedding(model_name=name)
        except Exception as e:
            log.info("curator embeddings disabled: %s", e)
            _model = None
    return _model


def embed(text: str) -> list[float] | None:
    m = _embedder()
    if m is None or not text.strip():
        return None
    try:
        return list(next(iter(m.embed([text]))))
    except Exception as e:
        log.warning("embed failed: %s", e)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def most_similar(vec: list[float], candidates: dict[str, list[float]]) -> tuple[str | None, float]:
    """Return (key, score) of the nearest candidate vector, or (None, 0.0)."""
    best_key, best = None, 0.0
    for key, cvec in candidates.items():
        s = cosine(vec, cvec)
        if s > best:
            best_key, best = key, s
    return best_key, best
