"""Gemini adapter — the default cheap cloud fallback for reasoning / deep work.

REST only (no SDK). The API key is read from the environment via config.api_key(), never from
tracked config. Disabled until a key is present.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from orion.core.config import config
from .base import BaseProvider, Message

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(BaseProvider):
    name = "gemini"
    is_local = False

    def _cfg(self) -> dict:
        return config.provider_cfg("gemini")

    def _key(self) -> str | None:
        return config.api_key("gemini")

    def _payload(self, messages: list[Message]) -> dict:
        system = [m.content for m in messages if m.role == "system"]
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages if m.role != "system"
        ]
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        return body

    async def is_available(self) -> bool:
        return bool(self._cfg().get("enabled") and self._key())

    def _url(self, method: str) -> str:
        model = self._cfg().get("model", "gemini-2.5-flash")
        return f"{_BASE}/{model}:{method}?key={self._key()}"

    async def complete(self, messages: list[Message], **kw) -> str:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(self._url("generateContent"), json=self._payload(messages))
            r.raise_for_status()
            cand = r.json().get("candidates", [{}])[0]
            return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))

    async def stream(self, messages: list[Message], **kw) -> AsyncIterator[str]:
        url = self._url("streamGenerateContent") + "&alt=sse"
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("POST", url, json=self._payload(messages)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        cand = json.loads(data).get("candidates", [{}])[0]
                    except json.JSONDecodeError:
                        continue
                    for p in cand.get("content", {}).get("parts", []):
                        if p.get("text"):
                            yield p["text"]
