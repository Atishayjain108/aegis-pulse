"""Additional coverage tests for aegis.evolve.hpo — proxy AUC, objective builders."""

from __future__ import annotations

import numpy as np
import pytest

from aegis.evolve.hpo import (
    _build_objective,
    _proxy_auc,
    optimize_hyperparameters,
)


def _xy(n: int = 120) -> tuple:
    rng = np.random.default_rng(7)
    X = rng.standard_normal((n, 20))
    y = (rng.random(n) > 0.5).astype(int)
    h = n // 2
    return X[:h], y[:h], X[h:], y[h:]


class TestProxyAUC:
    def test_returns_float_in_range(self) -> None:
        X_tr, y_tr, X_vl, y_vl = _xy()
        auc = _proxy_auc(X_tr, y_tr, X_vl, y_vl, {})
        assert 0.0 <= auc <= 1.0

    def test_sklearn_absent_raises_not_fabricates(self) -> None:
        # OMEGA reality-first (FIX-5): when the proxy cannot compute a real AUC
        # it must raise, never invent a score. Optuna then marks the trial failed
        # and HPO degrades to documented defaults instead of noise.
        import sys

        from aegis.evolve.errors import HPOFailedError

        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(sys.modules, "sklearn", None)
            mp.setitem(sys.modules, "sklearn.linear_model", None)
            mp.setitem(sys.modules, "sklearn.metrics", None)
            mp.setitem(sys.modules, "sklearn.preprocessing", None)
            X_tr, y_tr, X_vl, y_vl = _xy()
            with pytest.raises(HPOFailedError):
                _proxy_auc(X_tr, y_tr, X_vl, y_vl, {})

    def test_single_class_labels_raise(self) -> None:
        from aegis.evolve.errors import HPOFailedError

        X_tr, y_tr, X_vl, _ = _xy()
        y_vl_single = np.zeros(60, dtype=int)  # all same class → roc_auc raises
        with pytest.raises(HPOFailedError):
            _proxy_auc(X_tr, y_tr, X_vl, y_vl_single, {})


class TestBuildObjective:
    def _make_trial_mock(self, arch: str):
        """Return a simple namespace that mimics optuna.trial.Trial."""
        class FakeTrial:
            number = 0
            def suggest_int(self, name, low, high, step=1):
                return (low + high) // 2
            def suggest_float(self, name, low, high, log=False):
                return (low + high) / 2
            def suggest_categorical(self, name, choices):
                return choices[0]
        return FakeTrial()

    def test_patchts_objective(self) -> None:
        X_tr, y_tr, X_vl, y_vl = _xy()
        obj = _build_objective("patchts", X_tr, y_tr, X_vl, y_vl)
        trial = self._make_trial_mock("patchts")
        result = obj(trial)
        assert 0.0 <= result <= 1.0

    def test_autoformer_objective(self) -> None:
        X_tr, y_tr, X_vl, y_vl = _xy()
        obj = _build_objective("autoformer", X_tr, y_tr, X_vl, y_vl)
        trial = self._make_trial_mock("autoformer")
        result = obj(trial)
        assert 0.0 <= result <= 1.0

    def test_heuristic_objective(self) -> None:
        X_tr, y_tr, X_vl, y_vl = _xy()
        obj = _build_objective("heuristic", X_tr, y_tr, X_vl, y_vl)
        trial = self._make_trial_mock("heuristic")
        result = obj(trial)
        assert 0.0 <= result <= 1.0

    def test_unknown_architecture_raises(self) -> None:
        X_tr, y_tr, X_vl, y_vl = _xy()
        obj = _build_objective("bad_arch", X_tr, y_tr, X_vl, y_vl)
        trial = self._make_trial_mock("bad_arch")
        with pytest.raises(ValueError, match="Unknown architecture"):
            obj(trial)


class TestOptimizeHyperparametersWithOptuna:
    @pytest.mark.asyncio
    async def test_optuna_runs_and_returns_params(self) -> None:
        """Run a real 2-trial Optuna study if optuna is installed."""
        pytest.importorskip("optuna")
        X_tr, y_tr, X_vl, y_vl = _xy()
        hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "heuristic", n_trials=2)
        assert isinstance(hp, dict)
        assert "kelly_fraction" in hp

    @pytest.mark.asyncio
    async def test_optuna_patchts(self) -> None:
        pytest.importorskip("optuna")
        X_tr, y_tr, X_vl, y_vl = _xy()
        hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "patchts", n_trials=2)
        assert isinstance(hp, dict)
        assert "d_model" in hp

    @pytest.mark.asyncio
    async def test_optuna_autoformer(self) -> None:
        pytest.importorskip("optuna")
        X_tr, y_tr, X_vl, y_vl = _xy()
        hp = await optimize_hyperparameters(X_tr, y_tr, X_vl, y_vl, "autoformer", n_trials=2)
        assert isinstance(hp, dict)
        assert "decomp_method" in hp
