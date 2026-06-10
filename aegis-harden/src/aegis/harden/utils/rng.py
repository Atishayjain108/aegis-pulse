"""
Seeded RNG helper.

Phase 5 doctrine: every random selection (fingerprint pick, jitter, smoothing
noise) MUST be reproducible from a seed. This module is the only place we
import `random` or `numpy.random` directly.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import TypeVar

import numpy as np

from aegis.harden.constants import DEFAULT_RNG_SEED

T = TypeVar("T")


class SeededRng:
    """Wraps a stdlib `random.Random` AND a `numpy.random.Generator`.

    Both are seeded from the same root seed via independent SeedSequences,
    so stdlib operations don't perturb numpy operations and vice-versa.
    """

    __slots__ = ("_np", "_py", "_seed")

    def __init__(self, seed: int | None = None) -> None:
        self._seed = int(seed) if seed is not None else DEFAULT_RNG_SEED
        ss = np.random.SeedSequence(self._seed)
        py_seed, np_seed = ss.spawn(2)
        self._py = random.Random(int(py_seed.generate_state(1)[0]))
        self._np = np.random.default_rng(np_seed)

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def py(self) -> random.Random:
        return self._py

    @property
    def np(self) -> np.random.Generator:
        return self._np

    def choice(self, items: Sequence[T]) -> T:
        """Deterministic choice from a non-empty sequence."""
        if not items:
            raise IndexError("cannot choose from empty sequence")
        return self._py.choice(list(items))

    def jitter_ms(self, base_ms: int, spread_ms: int) -> int:
        """Symmetric uniform jitter around `base_ms`, clamped non-negative."""
        if spread_ms <= 0:
            return max(0, base_ms)
        return max(0, base_ms + self._py.randint(-spread_ms, spread_ms))


def default_rng() -> SeededRng:
    """Get a singleton-style default RNG. Useful for ad-hoc deterministic ops."""
    return SeededRng(DEFAULT_RNG_SEED)


__all__ = ["SeededRng", "default_rng"]
