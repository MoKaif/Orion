"""Anthropic (Claude) adapter — the API-based main orchestrator / reasoning provider.

This is the cloud brain: reasoning and deep-work turns route here while the local Ollama
model keeps handling the cheap reflex/dispatch/compress work. Uses the official `anthropic`
AsyncAnthropic SDK, lazy-imported so a missing package degrades gracefully (the router just
skips this provider and falls back to local). The API key is resolved via
``config.api_key("anthropic")`` from the environment / gitignored ``config/secrets.json`` —
never from tracked config.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from orion.core.config import config
from .base import BaseProvider, Message, Usage


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    is_local = False

    def __init__(self) -> None:
        self._last_usage: Usage | None = None

    def pop_last_usage(self) -> Usage | None:
        """Real usage from the most recent turn, consumed once by the router for costing."""
        usage, self._last_usage = self._last_usage, None
        return usage

    def _cfg(self) -> dict:
        return config.provider_cfg("anthropic")

    def _key(self) -> str | None:
        return config.api_key("anthropic")

    def _client(self):
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=self._key())

    def _model_for(self, mode: Any) -> str:
        """Mode→model tiering: route reasoning/deep_work to different-cost models when
        configured (``providers.anthropic.mode_models``), else the default model."""
        cfg = self._cfg()
        tier = getattr(mode, "value", mode)
        return cfg.get("mode_models", {}).get(tier) or cfg.get("model", "claude-opus-4-8")

    def _split(self, messages: list[Message]) -> tuple[list[dict], list[dict]]:
        """Claude takes the system prompt out-of-band; turns must be user/assistant only.

        System content becomes a list of text blocks so the *stable* prefix (constitution +
        specialist) can carry a ``cache_control`` breakpoint — the volatile world-model
        context that follows is a separate, uncached block, so it never invalidates the
        cached prefix. Prompt caching is prefix-match: order is preserved as given.
        """
        system: list[dict] = []
        for m in messages:
            if m.role != "system":
                continue
            block: dict = {"type": "text", "text": m.content}
            if m.cacheable:
                block["cache_control"] = {"type": "ephemeral"}
            system.append(block)
        turns = [
            {"role": "assistant" if m.role == "assistant" else "user", "content": m.content}
            for m in messages if m.role != "system"
        ]
        return system, turns

    def _params(self, messages: list[Message], mode: Any = None) -> dict:
        cfg = self._cfg()
        system, turns = self._split(messages)
        params: dict = {
            "model": self._model_for(mode),
            "max_tokens": cfg.get("max_tokens", 4096),
            "messages": turns,
            # Top-level auto-caching: caches the last cacheable block, so the growing
            # conversation history gets incremental cache hits turn over turn. This is
            # additive to the block-level breakpoint on the stable system prefix below.
            # Caching silently no-ops until the prefix crosses the model's minimum
            # (~4096 tokens on Opus 4.8 / Haiku 4.5) — no error, just a cold write later.
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            params["system"] = system
        # effort is supported on Opus/Sonnet current-gen but rejected (400) by Haiku 4.5.
        if cfg.get("effort") and not params["model"].startswith("claude-haiku"):
            params["output_config"] = {"effort": cfg["effort"]}
        return params

    @staticmethod
    def _usage(raw: Any, model: str) -> Usage:
        return Usage(
            input_tokens=getattr(raw, "input_tokens", 0) or 0,
            output_tokens=getattr(raw, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
            model=model,
        )

    async def is_available(self) -> bool:
        if not self._cfg().get("enabled") or not self._key():
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    async def complete(self, messages: list[Message], mode: Any = None, **kw) -> str:
        params = self._params(messages, mode)
        async with self._client() as client:
            resp = await client.messages.create(**params)
            self._last_usage = self._usage(resp.usage, params["model"])
            return "".join(b.text for b in resp.content if b.type == "text")

    async def stream(self, messages: list[Message], mode: Any = None, **kw) -> AsyncIterator[str]:
        params = self._params(messages, mode)
        async with self._client() as client:
            async with client.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
                self._last_usage = self._usage(final.usage, params["model"])
