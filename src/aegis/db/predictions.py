"""Persist Phase 3 prediction bundles to the ``predictions`` table.

PROJECT OMEGA: for the whole project history the Phase 3 inference pipeline
*built* a ``PredictionRecord`` and then discarded it — ``predictions`` stayed
empty and the entire Predictive Apex layer was dead weight in production. This
module is the missing write path: it maps a ``PredictionBundle`` (produced by
both the in-process ``InferenceRunner`` and the HTTP ``/predict`` serving API)
onto the ``predictions`` table row.

Best-effort by contract — a persistence failure must NEVER break the agent
pipeline (inference is advisory). Idempotent via ``ON CONFLICT DO NOTHING`` on
the per-run ``correlation_id`` unique index.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from aegis.predict.schemas import PredictionBundle

_log = structlog.get_logger("aegis.db.predictions")

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


_PRED_NAMESPACE = uuid.UUID("a1e5c0de-0000-4000-8000-0a0e1500dc01")


def _model_kind_str(kind: Any) -> str:
    """ModelKind may be an enum or a plain string — normalise to text."""
    return getattr(kind, "value", None) or str(kind)


def _deterministic_pid(bundle: PredictionBundle, tenant_id: str) -> str:
    """Stable prediction_id for one (tenant, trend, feature-window, model).

    SCOUT and SENTINEL each run inference on the SAME signals → the same
    feature_window_hash and model_id — only their per-run correlation_id
    differs. Keying the row on the content (not the run) collapses that pair
    into ONE row and makes re-analysis idempotent instead of bloating the
    table 2× per trend on every pass.
    """
    key = f"{tenant_id}|{bundle.trend_id}|{bundle.feature_window_hash}|{bundle.model_id}"
    return str(uuid.uuid5(_PRED_NAMESPACE, key))


async def insert_prediction(
    pool: Any,
    bundle: PredictionBundle,
    *,
    tenant_id: str = _DEFAULT_TENANT,
    prediction_id: str | None = None,
    signature: str = "UNSIGNED",
) -> bool:
    """Insert one prediction bundle. Returns True on a fresh insert.

    Accepts a raw asyncpg pool or the ``PgPool`` wrapper (both expose
    ``acquire()``). Sets the RLS tenant GUC before writing. Never raises.
    """
    pid = prediction_id or _deterministic_pid(bundle, str(tenant_id))
    try:
        bundle_json = bundle.model_dump_json()
        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, false)", str(tenant_id)
            )
            status = await conn.execute(
                """
                INSERT INTO predictions (
                    prediction_id, tenant_id, trend_id, correlation_id,
                    model_id, model_name, model_kind, model_version,
                    schema_version, is_heuristic_only, seed,
                    started_at, finished_at, duration_ms,
                    feature_window_hash, bundle_json, signature
                )
                VALUES (
                    $1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16::jsonb, $17
                )
                ON CONFLICT (prediction_id) DO NOTHING
                """,
                pid,
                str(tenant_id),
                bundle.trend_id,
                bundle.correlation_id,
                bundle.model_id,
                bundle.model_name,
                _model_kind_str(bundle.model_kind),
                bundle.model_version,
                bundle.schema_version,
                bool(bundle.is_heuristic_only),
                int(bundle.seed),
                bundle.started_at,
                bundle.finished_at,
                float(bundle.duration_ms),
                bundle.feature_window_hash,
                bundle_json,
                signature,
            )
        inserted = status.endswith("1")
        _log.debug(
            "predictions.persisted",
            prediction_id=pid,
            trend_id=bundle.trend_id,
            inserted=inserted,
            heuristic_only=bundle.is_heuristic_only,
        )
        return inserted
    except Exception as exc:  # never break the pipeline on a persistence error
        _log.warning("predictions.persist_failed", error=str(exc)[:200])
        return False
