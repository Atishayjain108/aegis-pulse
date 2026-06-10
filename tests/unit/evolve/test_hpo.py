"""Unit tests for Phase 9 HPO module."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from aegis.evolve.constants import SUPPORTED_ARCHITECTURES
from aegis.evolve.hpo import get_default_hyperparameters, optimize_hyperparameters


def _make_data(n: int = 200, dim: int = 20) -> tuple:
    rng = np.random.default_rng(42)
    X = rng.standard_normal((n, dim))
    y = (rng.random(n) > 0.5).astype(int)
    n_tr = int(0.7 * n)
    n_vl = int(0.15 * n)
    return (
        X[:n_tr], y[:n_tr],
        X[n_tr: n_tr + n_vl], y[n_tr: n_tr + n_vl],
        X[n_tr + n_vl:], y[n_tr + n_vl:],
    )


class TestGetDefaultHyperparameters:
    def test_patchts(self) -> None:
        hp = get_default_hyperparameters("patchts")
        assert "d_model" in hp
        assert "lr" in hp
        assert "n_layers" in hp

    def test_autoformer(self) -> None:
        hp = get_default_hyperparameters("autoformer")
        assert "decomp_method" in hp

    def test_heuristic(self) -> None:
        hp = get_default_hyperparameters("heuristic")
        assert "kelly_fraction" in hp
        assert "confidence_floor" in hp

    def test_unknown_fallback(self) -> None:
        hp = get_default_hyperparameters("nonexistent_arch")
        assert "kelly_fraction" in hp  # falls back to heuristic defaults

    def test_returns_copy(self) -> None:
        hp1 = get_default_hyperparameters("patchts")
        hp2 = get_default_hyperparameters("patchts")
        hp1["d_model"] = 9999
        assert hp2["d_model"] != 9999


class TestOptimizeHyperparameters:
    @pytest.mark.asyncio
    async def test_fallback_when_optuna_absent(self) -> None:
        """When Optuna is not installed, fall back to defaults."""
        X_tr, y_tr, X_vl, y_vl, _, _ = _make_data()
        with patch.dict("sys.modules", {"optuna": None}):
            hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "patchts", n_trials=5)
        assert "d_model" in hp

    @pytest.mark.asyncio
    async def test_unknown_architecture_remapped(self) -> None:
        X_tr, y_tr, X_vl, y_vl, _, _ = _make_data()
        hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "bad_arch", n_trials=2)
        # Falls back gracefully
        assert isinstance(hp, dict)
        assert len(hp) > 0

    @pytest.mark.asyncio
    async def test_returns_dict(self) -> None:
        X_tr, y_tr, X_vl, y_vl, _, _ = _make_data()
        hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "heuristic", n_trials=3)
        assert isinstance(hp, dict)

    @pytest.mark.asyncio
    async def test_all_supported_architectures(self) -> None:
        X_tr, y_tr, X_vl, y_vl, _, _ = _make_data()
        for arch in SUPPORTED_ARCHITECTURES:
            hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, arch, n_trials=2)
            assert isinstance(hp, dict)

    @pytest.mark.asyncio
    async def test_hpo_exception_falls_back(self) -> None:
        X_tr, y_tr, X_vl, y_vl, _, _ = _make_data()
        with patch("aegis.evolve.hpo._build_objective", side_effect=RuntimeError("study error")):
            hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "patchts", n_trials=2)
        assert isinstance(hp, dict)
