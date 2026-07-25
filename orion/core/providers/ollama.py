"""Ollama adapter — the local reflex/dispatch/compress provider (free)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from orion.core.config import config
from .base import BaseProvider, Message


class OllamaProvider(BaseProvider):
    name = "ollama"
    is_local = True

    def _cfg(self) -> dict:
        return config.provider_cfg("ollama")

    def _payload(self, messages: list[Message], stream: bool) -> dict:
        cfg = self._cfg()
        return {
            "model": cfg.get("model", "qwen2.5:3b-instruct-q4_K_M"),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "keep_alive": "30m",  # fewer cold reloads on the CPU-only box
            "options": {"num_ctx": cfg.get("context_window", 4096)},
        }

    @staticmethod
    def _timeout() -> httpx.Timeout:
        """Generous read timeout: CPU prefill of a full pipeline prompt (constitution +
        recall + history) can exceed two minutes on the 8GB box — no bytes stream until
        prefill finishes, so a short read timeout kills the fallback exactly when the
        cloud is down and it matters most."""
        return httpx.Timeout(600, connect=5)

    async def is_available(self) -> bool:
        if not self._cfg().get("enabled", True):
            return False
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(f"{self._cfg().get('base_url')}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def complete(self, messages: list[Message], **kw) -> str:
        async with httpx.AsyncClient(timeout=self._timeout()) as c:
            r = await c.post(f"{self._cfg().get('base_url')}/api/chat",
                             json=self._payload(messages, stream=False))
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "")

    async def stream(self, messages: list[Message], **kw) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self._timeout()) as c:
            async with c.stream("POST", f"{self._cfg().get('base_url')}/api/chat",
                                 json=self._payload(messages, stream=True)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
