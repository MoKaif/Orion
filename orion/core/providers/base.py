"""Provider abstraction. OpenAI-compatible-first; per-provider adapters where needed.

A provider is a single LLM backend (Ollama, Gemini, a generic OpenAI-compatible endpoint,
Anthropic). Providers are detected by hostname, not substring (an odysseus lesson). Concrete
adapters land in M1 (ports of the MVP's Ollama/Gemini code, cleaned).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class Message:
    role: str
    content: str
    cacheable: bool = False   # stable prefix → provider may set a prompt-cache breakpoint here


@dataclass
class Usage:
    """Real per-turn accounting, reported back to the cost-aware router.

    Cloud providers fill every field (Claude reports cache reads/writes); local
    providers leave the cache fields at zero. ``model`` names the model that actually
    ran, so mode→model tiering is costed against the right price row.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_write_tokens)


class BaseProvider(ABC):
    name: str = "base"
    is_local: bool = False   # local providers are free and preferred; cloud is metered

    @abstractmethod
    async def complete(self, messages: list[Message], **kw) -> str: ...

    @abstractmethod
    def stream(self, messages: list[Message], **kw) -> AsyncIterator[str]: ...

    @abstractmethod
    async def is_available(self) -> bool: ...
