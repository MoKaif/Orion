"""Tool argument dispatch — turns a user message into structured tool args.

Uses the local model (free, task=dispatch) to extract args (default) or generate them (e.g. a
shell command). Returns ``None`` when no local provider is available or the model output can't
be parsed, so the orchestrator simply skips tool execution rather than failing the turn.
Carried from the MVP, cleaned.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from orion.core.providers import router
from orion.core.providers.base import Message
from orion.core.tools.base import BaseTool

log = logging.getLogger("orion.dispatcher")


def _parse(raw: str) -> dict[str, Any] | None:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None


async def extract_args(tool: BaseTool, message: str) -> dict[str, Any] | None:
    provider = router.get("ollama")
    if provider is None or not await provider.is_available():
        return None

    verb = "Compose" if tool.dispatch_mode == "generate" else "Extract"
    prompt = (
        f"{verb} arguments for the tool '{tool.name}' ({tool.description}).\n"
        f"Argument schema (JSON): {json.dumps(tool.args_schema)}\n"
        f"User message: {message}\n"
        "Return ONLY a JSON object of the arguments. No prose."
    )
    try:
        raw = await provider.complete([Message("user", prompt)])
        return _parse(raw)
    except Exception as e:
        log.info("arg extraction failed for %s: %s", tool.name, e)
        return None
