"""SwarmResult and WaveStats — canonical schemas for multi-wave swarm output."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WaveStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    wave_number: int
    agents_run: int
    signals_collected: int
    duration_ms: float
    failures: int


class SwarmResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime
    finished_at: datetime
    total_signals: int
    unique_signals: int
    dedup_removed: int
    by_platform: dict[str, int]
    by_tier: dict[str, int]
    wave_stats: list[WaveStats]
    top_patterns: list[dict] = Field(default_factory=list)
    batch_confidence: float = 1.0
    cross_platform_themes: list[str] = Field(default_factory=list)
    hot_categories: list[str] = Field(default_factory=list)
    market_pulse: str = "neutral"
    conclusion: str = ""
    creator_graph_metrics: dict | None = None


__all__ = ["SwarmResult", "WaveStats"]
