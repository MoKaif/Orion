"""The World Model — the heart of Orion and its one permanent asset.

Entities, relationships, and knowledge (facts / observations / ideas) with confidence and a
review-inbox lifecycle, plus semantic recall over local embeddings. Entity and relationship
*types* are extensible by plugins.

M0 ships the schema and the ``WorldModel`` interface. M1 fills in the store and vector recall.
See docs/ARCHITECTURE.md §4.
"""
from .store import WorldModel, world_model

__all__ = ["WorldModel", "world_model"]
