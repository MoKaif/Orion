"""Software plugin — code/repo tools + a software specialist."""
from orion.core import specialists
from orion.core.specialists import Specialist
from orion.core.tools import registry


class SoftwareSpecialist(Specialist):
    name = "software"
    description = "Writes and reviews code, works with repos, runs commands."
    keywords = ("code", "bug", "refactor", "function", "repo", "git", "compile", "test",
                "implement", "debug", "run command", "script", "file")
    tools = ("read_file", "shell", "vault_search")

    def system_fragment(self) -> str:
        return ("You are acting as the Software specialist. Be precise and idiomatic. Prefer "
                "reading files before proposing changes. Never run irreversible commands without "
                "the user's confirmation.")


def register() -> None:
    from .tools import ReadFileTool, ShellTool
    registry.register(ReadFileTool())
    registry.register(ShellTool())
    specialists.register(SoftwareSpecialist())
