"""ADP-4 / BRAIN-2 — adaptive scrape-budget allocation via UCB1.

The swarm historically gave every adapter the same per-run ``limit`` regardless
of how productive it was: a permanently-empty source cost exactly as much budget
as a surging one. This module treats each adapter as an arm of a multi-armed
bandit and allocates the per-run fetch budget with the **UCB1** rule
(``mean_reward + c·sqrt(ln N / n)``), so coverage concentrates where signal is
while still periodically re-exploring quiet sources (they never starve below
``min_limit``).

Reward = signals yielded on a run (the caller passes the raw count; the allocator
normalises internally). State is in-process and cheap; a single allocator lives on
the ``SwarmOrchestrator``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import structlog

_log = structlog.get_logger("aegis.scrape.budget")

# UCB1 exploration constant. Higher → more re-exploration of quiet arms.
_DEFAULT_C = 1.4
# Reward saturates here so one viral run can't dominate the mean forever.
_REWARD_CAP = 100.0
# PASS2-2B: normalised-yield ceiling — signals per second of wall time above
# which extra speed earns no additional reward (prevents extremely fast but
# low-quality sources from dominating the bandit).
_YIELD_PER_SECOND_CAP = 100.0


@dataclass
class _Arm:
    plays: int = 0
    reward_sum: float = 0.0  # sum of normalised rewards in [0, 1]

    @property
    def mean(self) -> float:
        return self.reward_sum / self.plays if self.plays else 0.0


@dataclass
class UCB1Allocator:
    """Distributes a per-run scrape budget across adapters by UCB1 score."""

    c: float = _DEFAULT_C
    reward_cap: float = _REWARD_CAP
    _arms: dict[str, _Arm] = field(default_factory=dict)
    _total_plays: int = 0

    def record(self, arm: str, signals: int) -> None:
        """Fold one run's yield into the arm's reward history."""
        reward = min(max(signals, 0), self.reward_cap) / self.reward_cap
        self._record_reward(arm, reward)

    def record_with_quality(
        self,
        source: str,
        *,
        yield_count: int,
        elapsed_s: float,
        novelty_fraction: float,
        avg_confidence: float,
    ) -> None:
        """PASS2-2B: record a composite quality reward, not a raw count.

        reward = 0.5 × normalized_yield
               + 0.3 × novelty_fraction   (fraction not seen in last 24 h)
               + 0.2 × avg_confidence     (mean confidence of yielded signals)

        Normalized yield is signals per second of wall time, capped at
        ``_YIELD_PER_SECOND_CAP``. A source producing 200 duplicate
        low-confidence signals scores below one producing 20 novel,
        high-confidence ones — the bandit rewards quality, not quantity.
        """
        if elapsed_s <= 0:
            elapsed_s = 1.0
        normalized_yield = min(
            1.0, max(yield_count, 0) / max(elapsed_s * _YIELD_PER_SECOND_CAP, 1.0)
        )
        composite = (
            0.5 * normalized_yield
            + 0.3 * max(0.0, min(1.0, novelty_fraction))
            + 0.2 * max(0.0, min(1.0, avg_confidence))
        )
        self._record_reward(source, composite)
        _log.debug(
            "ucb1.quality_reward",
            source=source,
            yield_count=yield_count,
            elapsed_s=round(elapsed_s, 2),
            novelty=round(novelty_fraction, 3),
            avg_confidence=round(avg_confidence, 3),
            composite=round(composite, 4),
        )

    def _record_reward(self, arm: str, reward: float) -> None:
        """Fold one normalised reward in [0, 1] into the arm's history."""
        a = self._arms.setdefault(arm, _Arm())
        a.plays += 1
        a.reward_sum += max(0.0, min(1.0, reward))
        self._total_plays += 1

    def ucb_score(self, arm: str) -> float:
        """UCB1 score for one arm. Unplayed arms score ``inf`` (forced explore)."""
        a = self._arms.get(arm)
        if a is None or a.plays == 0:
            return math.inf
        bonus = self.c * math.sqrt(math.log(max(self._total_plays, 1)) / a.plays)
        return a.mean + bonus

    def allocate(
        self,
        arms: list[str],
        *,
        base_limit: int,
        min_limit: int = 5,
        max_limit: int | None = None,
    ) -> dict[str, int]:
        """Return a per-arm fetch limit.

        Unexplored arms get ``base_limit`` (forced exploration). Explored arms are
        scaled by their UCB score relative to the playing-arm mean, clamped to
        ``[min_limit, max_limit]`` so productive sources get more and quiet ones
        stay alive without burning budget.
        """
        if not arms:
            return {}
        if max_limit is None:
            max_limit = base_limit * 2

        finite = {a: self.ucb_score(a) for a in arms if math.isfinite(self.ucb_score(a))}
        mean_score = (sum(finite.values()) / len(finite)) if finite else 0.0

        out: dict[str, int] = {}
        for arm in arms:
            score = self.ucb_score(arm)
            if math.isinf(score):
                out[arm] = base_limit  # never-played → explore at the baseline
                continue
            if mean_score <= 0:
                out[arm] = base_limit
                continue
            scaled = round(base_limit * (score / mean_score))
            out[arm] = max(min_limit, min(max_limit, scaled))
        return out

    def stats(self) -> dict[str, dict[str, float]]:
        return {
            arm: {"plays": a.plays, "mean_reward": round(a.mean, 4)}
            for arm, a in self._arms.items()
        }


__all__ = ["UCB1Allocator"]
