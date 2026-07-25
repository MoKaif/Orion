"""DeepSeek adapter — the cheap cloud brain (OpenAI-compatible REST, no SDK).

Runs chat on ``deepseek-v4-flash`` ($0.14/M in, $0.28/M out, $0.0028/M cached input as of
2026-07). DeepSeek caches context automatically server-side — no cache_control markers
needed; the usage block splits prompt tokens into cache hits/misses and the router prices
each side (see config/models.json "pricing"). The API key resolves from the environment via
``config.api_key("deepseek")`` (seeded from gitignored config/secrets.json) — never from
tracked config. Missing key or package ⇒ ``is_available()`` False ⇒ router escalates to
local, per the everything-degrades-gracefully rule.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from orion.core.config import config
from .base import BaseProvider, Message, Usage


class DeepSeekProvider(BaseProvider):
    name = "deepseek"
    is_local = False

    def __init__(self) -> None:
        self._last_usage: Usage | None = None

    def pop_last_usage(self) -> Usage | None:
        """Real usage from the most recent turn, consumed once by the router for costing."""
        usage, self._last_usage = self._last_usage, None
        return usage

    def _cfg(self) -> dict:
        return config.provider_cfg("deepseek")

    def _key(self) -> str | None:
        return config.api_key("deepseek")

    def _url(self) -> str:
        return f"{self._cfg().get('base_url', 'https://api.deepseek.com')}/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"}

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(180, connect=10)

    def _payload(self, messages: list[Message], stream: bool) -> dict:
        cfg = self._cfg()
        # OpenAI-compatible shape: one leading system message, then user/assistant turns.
        system = [m.content for m in messages if m.role == "system"]
        turns: list[dict] = []
        if system:
            turns.append({"role": "system", "content": "\n\n".join(system)})
        turns += [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        body: dict = {
            "model": cfg.get("model", "deepseek-v4-flash"),
            "messages": turns,
            "stream": stream,
            "max_tokens": cfg.get("max_tokens", 4096),
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _usage(self, raw: dict[str, Any] | None, model: str) -> Usage:
        raw = raw or {}
        hits = raw.get("prompt_cache_hit_tokens", 0) or 0
        misses = raw.get("prompt_cache_miss_tokens")
        if misses is None:  # fall back if the split isn't reported
            misses = (raw.get("prompt_tokens", 0) or 0) - hits
        return Usage(
            input_tokens=max(misses, 0),
            output_tokens=raw.get("completion_tokens", 0) or 0,
            cache_read_tokens=hits,
            cache_write_tokens=0,   # DeepSeek charges no cache-write premium
            model=model,
        )

    async def is_available(self) -> bool:
        return bool(self._cfg().get("enabled") and self._key())

    async def complete(self, messages: list[Message], **kw) -> str:
        payload = self._payload(messages, stream=False)
        async with httpx.AsyncClient(timeout=self._timeout()) as c:
            r = await c.post(self._url(), headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
            self._last_usage = self._usage(data.get("usage"), payload["model"])
            return data["choices"][0]["message"].get("content", "") or ""

    async def stream(self, messages: list[Message], **kw) -> AsyncIterator[str]:
        payload = self._payload(messages, stream=True)
        async with httpx.AsyncClient(timeout=self._timeout()) as c:
            async with c.stream("POST", self._url(), headers=self._headers(),
                                json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("usage"):  # final chunk carries the usage block
                        self._last_usage = self._usage(chunk["usage"], payload["model"])
                    for choice in chunk.get("choices", []):
                        token = (choice.get("delta") or {}).get("content")
                        if token:
                            yield token
