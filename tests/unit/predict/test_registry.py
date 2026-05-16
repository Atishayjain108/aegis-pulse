"""Tests for `aegis.predict.registry`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aegis.predict.backtest.runner import AggregatedMetrics
from aegis.predict.errors import (
    ModelHashMismatchError,
    ModelNotFoundError,
)
from aegis.predict.registry import (
    ModelStore,
    PromotionGate,
    evaluate_promotion,
    evaluate_rollback,
)
from aegis.predict.schemas import ModelKind, ModelManifest


def _mk_manifest(
    *,
    name: str = "patchtst",
    version: str = "1.0.0",
    sha256: str = "",
) -> ModelManifest:
    now = datetime.now(UTC)
    return ModelManifest(
        model_id=f"{name}@{version}",
        name=name,
        kind=ModelKind.TEMPORAL,
        version=version,
        weights_uri=f"s3://bucket/{name}/{version}.bin",
        sha256=sha256 or ("0" * 64),
        trained_at=now,
        train_window_start=now - timedelta(days=60),
        train_window_end=now,
        n_train_samples=10_000,
    )


class TestModelStore:
    def test_register_and_retrieve(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        manifest = _mk_manifest()
        artifact = b"some bytes" * 100
        registered = store.register(manifest, artifact)
        assert registered.sha256 != "0" * 64  # hash overwritten with real
        loaded = store.load_artifact("patchtst", "1.0.0")
        assert loaded == artifact

    def test_register_with_correct_hash(self, tmp_path: Path):
        import hashlib

        artifact = b"deterministic"
        sha = hashlib.sha256(artifact).hexdigest()
        store = ModelStore(root=tmp_path)
        store.register(_mk_manifest(sha256=sha), artifact)
        loaded = store.load_artifact("patchtst", "1.0.0")
        assert loaded == artifact

    def test_register_with_wrong_hash_raises(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        with pytest.raises(ModelHashMismatchError):
            store.register(_mk_manifest(sha256="a" * 64), b"different bytes")

    def test_load_after_artifact_corruption(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        store.register(_mk_manifest(), b"original bytes")
        artifact_path = tmp_path / "artifacts" / "patchtst" / "1.0.0.bin"
        artifact_path.write_bytes(b"tampered bytes")
        with pytest.raises(ModelHashMismatchError):
            store.load_artifact("patchtst", "1.0.0")

    def test_promote_updates_index(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        store.register(_mk_manifest(version="1.0.0"), b"v1")
        store.register(_mk_manifest(version="2.0.0"), b"v2")
        assert store.get_production("patchtst") is None  # nothing promoted yet
        store.promote("patchtst", "1.0.0")
        prod = store.get_production("patchtst")
        assert prod is not None
        assert prod.version == "1.0.0"
        # Promote a newer version → old one auto-archives.
        store.promote("patchtst", "2.0.0")
        prod = store.get_production("patchtst")
        assert prod.version == "2.0.0"
        archived = store.get_manifest("patchtst", "1.0.0")
        assert archived.stage == "archived"

    def test_archive_removes_from_index(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        store.register(_mk_manifest(), b"v1")
        store.promote("patchtst", "1.0.0")
        store.archive("patchtst", "1.0.0")
        assert store.get_production("patchtst") is None

    def test_get_unknown_raises(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        with pytest.raises(ModelNotFoundError):
            store.get_manifest("does_not_exist", "1.0.0")

    def test_promote_archived_refused(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        store.register(_mk_manifest(), b"v1")
        store.promote("patchtst", "1.0.0")
        store.archive("patchtst", "1.0.0")
        with pytest.raises(Exception):
            store.promote("patchtst", "1.0.0")

    def test_list_models_and_versions(self, tmp_path: Path):
        store = ModelStore(root=tmp_path)
        store.register(_mk_manifest(name="patchtst", version="1.0.0"), b"a")
        store.register(_mk_manifest(name="patchtst", version="1.1.0"), b"b")
        store.register(_mk_manifest(name="autoformer", version="0.1.0"), b"c")
        assert sorted(store.list_models()) == ["autoformer", "patchtst"]
        assert sorted(store.list_versions("patchtst")) == ["1.0.0", "1.1.0"]


def _agg(**overrides) -> AggregatedMetrics:
    base = {
        "n_folds": 5,
        "n_test_total": 2000,
        "accuracy": 0.7,
        "macro_f1": 0.65,
        "breakout_precision": 0.80,
        "breakout_recall": 0.55,
        "mae_log_velocity": 0.20,
        "coverage_90": 0.92,
        "sharpness": 1.2,
        "ece": 0.05,
        "brier_breakout": 0.18,
    }
    base.update(overrides)
    return AggregatedMetrics(**base)


class TestPromotionGate:
    def test_no_incumbent_goes_to_shadow(self):
        d = evaluate_promotion(_agg(), incumbent=None)
        assert d.promote is True
        assert d.suggested_stage == "shadow"

    def test_insufficient_samples_blocks(self):
        d = evaluate_promotion(_agg(n_test_total=10), incumbent=_agg())
        assert d.promote is False
        assert any("insufficient" in r for r in d.reasons)

    def test_better_f1_promotes(self):
        cand = _agg(macro_f1=0.70)
        inc = _agg(macro_f1=0.65)
        d = evaluate_promotion(cand, inc)
        assert d.promote is True
        assert d.suggested_stage == "shadow"

    def test_within_margin_blocks(self):
        cand = _agg(macro_f1=0.66)
        inc = _agg(macro_f1=0.65)  # delta=0.01 < margin=0.02
        d = evaluate_promotion(cand, inc)
        assert d.promote is False

    def test_precision_regression_blocks(self):
        # Even if F1 is better, precision drop > tolerance blocks.
        cand = _agg(macro_f1=0.75, breakout_precision=0.65)
        inc = _agg(macro_f1=0.65, breakout_precision=0.85)
        d = evaluate_promotion(cand, inc)
        assert d.promote is False
        assert any("precision_regression" in r for r in d.reasons)

    def test_evaluate_rollback_triggers(self):
        live = _agg(breakout_precision=0.50)
        prom = _agg(breakout_precision=0.85)
        d = evaluate_rollback(live, prom)
        assert d.promote is True
        assert d.suggested_stage == "archived"

    def test_evaluate_rollback_no_action_when_within_tolerance(self):
        live = _agg(breakout_precision=0.83)
        prom = _agg(breakout_precision=0.85)
        d = evaluate_rollback(live, prom)
        assert d.promote is False
        assert d.suggested_stage == "production"

    def test_custom_gate(self):
        gate = PromotionGate(f1_margin=0.10)
        cand = _agg(macro_f1=0.70)
        inc = _agg(macro_f1=0.65)  # delta=0.05 < custom margin 0.10
        d = evaluate_promotion(cand, inc, gate=gate)
        assert d.promote is False
