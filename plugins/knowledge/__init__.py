"""Knowledge plugin — ingests an Obsidian vault into the World Model as note entities and
indexes them for semantic recall, exposes search/read tools, an hourly indexing job, and a
mission-control widget. Everything here goes through ``orion.core.plugin_sdk`` — it never
imports a core registry directly. See plugins/README.md.
"""
from orion.core import plugin_sdk as orion


def register() -> None:
    from .tools import VaultSearchTool, VaultReadTool
    orion.add_tool(VaultSearchTool())
    orion.add_tool(VaultReadTool())

    # Describe the world-model types this plugin introduces (manifest also declares them).
    orion.add_entity_type("note", "An Obsidian note ingested from the vault.", plugin="knowledge")
    orion.add_entity_type("concept", "A concept mentioned across notes.", plugin="knowledge")
    orion.add_relationship_type("mentions", "A note mentions a concept/entity.", plugin="knowledge")
    orion.add_relationship_type("originated_from", "Knowledge derived from a note.",
                                plugin="knowledge")

    # Hourly vault indexing (idle-by-default, preempted by interactive chat).
    from .ingest import ingest_vault

    async def _index_vault():
        return ingest_vault()

    orion.add_job("index_vault", "0 * * * *", _index_vault)

    # Mission-control widget declared in the manifest: this plugin's slice of the world model
    # (its own entity types), distinct from the core dashboard cards.
    orion.add_widget("recent_discoveries", "Vault knowledge", _render_domain, plugin="knowledge")


def _render_domain() -> str:
    """Server-rendered card body: how much of each knowledge-plugin type the model holds."""
    from html import escape
    from orion.core.world_model import world_model

    counts = world_model.type_counts()
    owned = ("note", "concept")  # the types this plugin contributes
    rows = [(t, counts.get(t, 0)) for t in owned]
    if not any(c for _, c in rows):
        return '<p class="empty">No notes ingested yet. Run the vault index to populate this.</p>'
    chips = "".join(
        f'<li><span class="ws-facts">{c}</span> {escape(t)}{"s" if c != 1 else ""}</li>'
        for t, c in rows
    )
    return f'<ul class="ws-list">{chips}</ul>'
