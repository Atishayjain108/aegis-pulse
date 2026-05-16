"""
Feature window builder — Phase 1 ↔ Phase 3 bridge.

`build_feature_window(...)` is the public entry point. Given a
trend_id, a tenant_id, and a window-end timestamp, it:
    1. Pulls all signals in [end - WINDOW, end] from the Phase 1 DB.
    2. Buckets them into hourly slots.
    3. Computes the 20-D per-bucket feature vector defined by
       FEATURE_NAMES.
    4. Returns a frozen, validated FeatureWindow.

If the DB pool is unavailable (testing, dry-run) or the row set is
empty, the function still produces a valid all-zeros FeatureWindow
so downstream models always have a tensor to consume.

The function exposes two layers:
    - `build_window_from_rows(...)` — pure, takes a list of dict rows.
      Trivially testable, no I/O.
    - `build_feature_window(...)`   — async, hits the DB, calls the
      pure layer.

Author: AEGIS Pulse core team
"""

from __future__ import annotations

import hashlib
import math
import uuid as _uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from .. import (
    DEFAULT_FEATURE_WINDOW,
    FEATURE_DIM,
    FEATURE_NAMES,
)
from ..constants import ENGAGEMENT_LOG1P, FEATURE_WINDOW_HOURS
from ..schemas import FeatureWindow
from .velocity import compute_velocities

_log = structlog.get_logger("aegis.predict.features.builder")

# Feature index lookup for self-documenting writes.
_FIDX: dict[str, int] = {n: i for i, n in enumerate(FEATURE_NAMES)}


def _hash_window(values: list[float]) -> str:
    """Stable short hash of the values vector — used for replay."""
    h = hashlib.sha256()
    # Round to 6 decimals before hashing so trivial float drift
    # doesn't change the hash across re-runs of the same input.
    for v in values:
        h.update(f"{round(v, 6):.6f}|".encode())
    return h.hexdigest()[:16]


def _platform_entropy(platforms: Counter[str]) -> float:
    """Shannon entropy across platforms, normalized to [0, 1]."""
    total = sum(platforms.values())
    if total <= 0 or len(platforms) <= 1:
        return 0.0
    p = [c / total for c in platforms.values()]
    H = -sum(pi * math.log(pi) for pi in p if pi > 0)
    H_max = math.log(len(platforms))
    return H / H_max if H_max > 0 else 0.0


def _bucket_index(ts: datetime, window_end: datetime, window_size: int) -> int:
    """Return the bucket index in [0, window_size-1] for `ts`.

    Bucket 0 = oldest hour, bucket window_size-1 = most recent
    (the hour ending at `window_end`).
    """
    delta_s = (window_end - ts).total_seconds()
    hours_back = int(delta_s // 3600)
    return window_size - 1 - hours_back


def build_window_from_rows(
    *,
    trend_id: str,
    tenant_id: str,
    rows: list[dict[str, Any]],
    window_end: datetime,
    window_size: int = DEFAULT_FEATURE_WINDOW,
    correlation_id: str | None = None,
) -> FeatureWindow:
    """Pure feature builder — no I/O, fully deterministic.

    Each row is a dict shaped like Phase 1's `fetch_recent_signals(...)`:
        - id (UUID), platform (str), captured_at (datetime, UTC),
        - title (str | None), body (str | None), url (str | None),
        - content_hash (str), author_id (str | None),
        - views, likes, comments, shares, saves (int | None),
        - sentiment (float | None), commercial_intent (float | None),
        - novelty (float | None).

    Missing optional fields are tolerated — they read as 0.0.
    """
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=UTC)
    else:
        window_end = window_end.astimezone(UTC)

    # Filter rows into the window and assign each to a bucket.
    window_start = window_end - timedelta(hours=window_size)
    bucketed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        ts = r.get("captured_at")
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        if ts < window_start or ts > window_end:
            continue
        idx = _bucket_index(ts, window_end, window_size)
        if 0 <= idx < window_size:
            bucketed[idx].append(r)

    # ---- Bucket-level aggregates first.
    counts = [float(len(bucketed.get(i, []))) for i in range(window_size)]
    velocities = compute_velocities(counts)

    # Pre-allocate the flat feature matrix.
    matrix: list[float] = [0.0] * (window_size * FEATURE_DIM)

    for i in range(window_size):
        bucket = bucketed.get(i, [])
        bucket_ts = window_end - timedelta(hours=(window_size - 1 - i))

        platforms_in_bucket: Counter[str] = Counter(b.get("platform", "?") for b in bucket)
        unique_authors = {b.get("author_id") for b in bucket if b.get("author_id")}

        # Engagement aggregates (log1p compressed).
        eng_total = 0.0
        comments = 0.0
        shares = 0.0
        saves = 0.0
        sentiments: list[float] = []
        commercial: list[float] = []
        novelty: list[float] = []
        for b in bucket:
            v = float(b.get("views") or 0)
            lk = float(b.get("likes") or 0)
            c = float(b.get("comments") or 0)
            s = float(b.get("shares") or 0)
            sv = float(b.get("saves") or 0)
            eng_total += v + lk + c + s + sv
            comments += c
            shares += s
            saves += sv
            if (sx := b.get("sentiment")) is not None:
                sentiments.append(float(sx))
            if (ci := b.get("commercial_intent")) is not None:
                commercial.append(float(ci))
            if (nv := b.get("novelty")) is not None:
                novelty.append(float(nv))

        if ENGAGEMENT_LOG1P:
            eng_total = math.log1p(eng_total)

        n_signals = len(bucket)
        n_authors = len(unique_authors)
        eng_per_author = eng_total / n_authors if n_authors > 0 else 0.0
        comment_density = comments / n_signals if n_signals > 0 else 0.0
        share_density = shares / n_signals if n_signals > 0 else 0.0
        save_density = saves / n_signals if n_signals > 0 else 0.0

        sentiment_mean = sum(sentiments) / len(sentiments) if sentiments else 0.0
        if len(sentiments) > 1:
            mu = sentiment_mean
            sentiment_std = math.sqrt(
                sum((x - mu) ** 2 for x in sentiments) / (len(sentiments) - 1)
            )
        else:
            sentiment_std = 0.0

        commercial_intent = sum(commercial) / len(commercial) if commercial else 0.0
        novelty_mean = sum(novelty) / len(novelty) if novelty else 0.0

        # Coordination risk: authors clustered (n_signals/n_authors)
        # within a single hour bucket — a noisy proxy. The proper
        # graph-level signal lives in the relational model.
        coord_risk = 0.0
        if n_authors > 0 and n_signals >= 4:
            ratio = n_signals / n_authors
            coord_risk = min(1.0, max(0.0, (ratio - 1.0) / 4.0))

        platform_div = _platform_entropy(platforms_in_bucket)

        # Time-of-day cyclic encoding (UTC).
        hod = bucket_ts.hour
        dow = bucket_ts.weekday()

        offset = i * FEATURE_DIM
        matrix[offset + _FIDX["signal_count"]] = float(n_signals)
        matrix[offset + _FIDX["unique_authors"]] = float(n_authors)
        matrix[offset + _FIDX["platform_diversity"]] = platform_div
        matrix[offset + _FIDX["velocity_1h"]] = velocities.v1[i]
        matrix[offset + _FIDX["velocity_6h"]] = velocities.v6[i]
        matrix[offset + _FIDX["velocity_24h"]] = velocities.v24[i]
        matrix[offset + _FIDX["sentiment_mean"]] = sentiment_mean
        matrix[offset + _FIDX["sentiment_std"]] = sentiment_std
        matrix[offset + _FIDX["commercial_intent"]] = commercial_intent
        matrix[offset + _FIDX["novelty"]] = novelty_mean
        matrix[offset + _FIDX["coordination_risk"]] = coord_risk
        matrix[offset + _FIDX["engagement_total"]] = eng_total
        matrix[offset + _FIDX["engagement_per_author"]] = eng_per_author
        matrix[offset + _FIDX["comment_density"]] = comment_density
        matrix[offset + _FIDX["share_density"]] = share_density
        matrix[offset + _FIDX["save_density"]] = save_density
        matrix[offset + _FIDX["hour_of_day_sin"]] = math.sin(2 * math.pi * hod / 24)
        matrix[offset + _FIDX["hour_of_day_cos"]] = math.cos(2 * math.pi * hod / 24)
        matrix[offset + _FIDX["day_of_week_sin"]] = math.sin(2 * math.pi * dow / 7)
        matrix[offset + _FIDX["day_of_week_cos"]] = math.cos(2 * math.pi * dow / 7)

    # Defensive: scrub any NaN/Inf that snuck through (a None field
    # converted to float() can produce NaN if the upstream emitted
    # the string "nan" by mistake).
    for i, v in enumerate(matrix):
        if math.isnan(v) or math.isinf(v):
            matrix[i] = 0.0

    return FeatureWindow(
        trend_id=trend_id,
        correlation_id=correlation_id or trend_id,
        window_size=window_size,
        feature_dim=FEATURE_DIM,
        feature_names=FEATURE_NAMES,
        values=matrix,
        captured_at=window_end,
        tenant_id=tenant_id,
    )


async def build_feature_window(
    *,
    trend_id: str,
    tenant_id: str = "default",
    window_end: datetime | None = None,
    window_size: int = FEATURE_WINDOW_HOURS,
    pool: Any | None = None,
    rows: list[dict[str, Any]] | None = None,
    correlation_id: str | None = None,
) -> FeatureWindow:
    """Async-friendly feature builder.

    If `rows` is supplied, no DB call is made — used by the runner
    to pass already-fetched signals or by tests for deterministic
    inputs.

    If `rows` is None and `pool` is supplied, calls Phase 1's
    `fetch_recent_signals` via a lazy import (so this module
    imports even when `aegis.db` isn't installed in the env).

    If both are None, returns an all-zeros window — keeps the
    pipeline functional during dry-runs.
    """
    if window_end is None:
        window_end = datetime.now(UTC)
    elif window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=UTC)

    if rows is None and pool is not None:
        try:
            # Lazy import: keep this module testable without aegis.db.
            from aegis.db.signals import fetch_recent_signals  # type: ignore

            since = window_end - timedelta(hours=window_size)
            # NB: fetch_recent_signals returns most-recent-first; we
            # don't care about order, the bucketer keys by timestamp.
            # The function takes `pool` as positional arg per Phase 1.
            try:
                tenant_uuid = _uuid.UUID(tenant_id)
            except ValueError:
                tenant_uuid = _uuid.UUID(int=0)
            db_rows = await fetch_recent_signals(
                pool,
                tenant_id=tenant_uuid,
                limit=10_000,
                since=since,
            )
            # Filter to this trend's signals if a `trend_id` column
            # exists. Phase 1 doesn't have one yet — we use the title
            # heuristic via the caller filtering instead.
            rows = list(db_rows)
        except Exception as exc:
            _log.warning(
                "build_feature_window.db_failed",
                trend_id=trend_id,
                error=str(exc),
            )
            rows = []
    elif rows is None:
        rows = []

    return build_window_from_rows(
        trend_id=trend_id,
        tenant_id=tenant_id,
        rows=rows,
        window_end=window_end,
        window_size=window_size,
        correlation_id=correlation_id,
    )


def feature_window_hash(window: FeatureWindow) -> str:
    """Public hash helper, used by the runner for replay artifacts."""
    return _hash_window(window.values)


__all__ = [
    "build_feature_window",
    "build_window_from_rows",
    "feature_window_hash",
]
