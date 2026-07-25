"""Research plugin — web search + a research specialist."""
from orion.core import specialists
from orion.core.specialists import Specialist
from orion.core.tools import registry


class ResearchSpecialist(Specialist):
    name = "research"
    description = "Finds information, compares options, summarizes sources."
    keywords = ("research", "compare", "find out", "look up", "sources", "latest",
                "what is", "how does", "summarize")
    tools = ("web_search", "vault_search")

    def system_fragment(self) -> str:
        return ("You are acting as the Research specialist. Consult known knowledge first; use "
                "web_search only for gaps or current information. Cite sources. Separate "
                "established facts from your inferences.")


def register() -> None:
    from .tools import WebSearchTool
    registry.register(WebSearchTool())
    specialists.register(ResearchSpecialist())
