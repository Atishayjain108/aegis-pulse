"""
Agent memory subsystem.

Three layers, deliberately separated by access pattern and lifetime:

  * `ChromaMemoryStore` — per-agent long-lived semantic memory.
    One Chroma collection per agent. Embeddings via local BGE-M3
    (or any sentence-transformers model). Used by HISTORIAN to
    surface analogous past trends, by NARRATIVE to detect repeated
    storylines, etc.

  * `SharedWorkingMemory` — short-lived, trend-scoped, Redis-backed
    Hash + sorted-set. Lives 24h. Lets agents in a single graph run
    leave breadcrumbs for each other (e.g., SCOUT writes the
    `velocity_class`, AUDITOR reads it).

  * `SnapshotManager` — checkpoints full agent state (pickle-safe
    dicts only) to MinIO on a 10-minute cadence. Used for crash
    recovery and post-hoc replay.
"""
from .chroma_store import ChromaMemoryStore
from .shared_memory import SharedWorkingMemory
from .snapshots import SnapshotManager

__all__ = [
    "ChromaMemoryStore",
    "SharedWorkingMemory",
    "SnapshotManager",
]
