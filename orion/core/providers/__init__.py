"""Model provider layer — local-first, cloud fallback, cost-metered.

Concrete adapters register themselves with the router on import.
"""
from .base import BaseProvider, Message, Usage
from . import router
from .ollama import OllamaProvider
from .gemini import GeminiProvider
from .anthropic import AnthropicProvider
from .deepseek import DeepSeekProvider

router.register("ollama", OllamaProvider())
router.register("gemini", GeminiProvider())
router.register("anthropic", AnthropicProvider())
router.register("deepseek", DeepSeekProvider())

__all__ = ["BaseProvider", "Message", "Usage", "router"]
