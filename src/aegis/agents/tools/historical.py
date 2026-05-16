"""
Tool: historical_lookup.

Searches the HISTORIAN agent's Chroma collection for past trends
that look like the current candidate. Returns up to k matches with
similarity scores.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import ToolResult, tool_call

if TYPE_CHECKING:
    from ..memory.chroma_store import ChromaMemoryStore


@tool_call("historical_lookup")
async def find_analogues(
    store: ChromaMemoryStore,
    *,
    query_text: str,
    k: int = 5,
    min_score: float = 0.55,
) -> ToolResult:
    """Top-k similar past-trend memories filtered by `min_score`."""
    records = await store.query(query_text=query_text, k=int(k))
    filtered = [r for r in records if r.score >= float(min_score)]

    out: list[dict[str, Any]] = [
        {
            "id": r.id,
            "text": r.text,
            "score": round(r.score, 4),
            "metadata": dict(r.metadata),
        }
        for r in filtered
    ]
    return ToolResult.success(out, count=len(out), considered=len(records))
