"""Research plugin tools — web search (DuckDuckGo HTML, no API key)."""
from __future__ import annotations

import html
import re
from typing import Any

import httpx

from orion.core.tools.base import BaseTool, ToolResult

_RESULT = re.compile(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for current information when local knowledge is insufficient."
    triggers = ("search the web", "look online", "google", "search online", "latest",
                "current", "look up online")
    args_schema = {"query": "str"}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(False, "no query provided")
        try:
            async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = await c.get("https://html.duckduckgo.com/html/", params={"q": query})
                r.raise_for_status()
        except Exception as e:
            return ToolResult(False, f"web search unavailable ({type(e).__name__}); offline?")
        results = []
        for href, title in _RESULT.findall(r.text)[:5]:
            results.append(f"- {html.unescape(_TAGS.sub('', title)).strip()} — {href}")
        if not results:
            return ToolResult(True, f"No web results for '{query}'.")
        return ToolResult(True, f"Web results for '{query}':\n" + "\n".join(results),
                          meta={"count": len(results)})
