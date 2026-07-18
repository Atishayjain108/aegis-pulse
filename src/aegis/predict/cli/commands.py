"""
Typer subcommands for `aegis predict ...`.

Each command is a thin shell over the in-process API. Heavy logic
(InferenceRunner, WalkForwardBacktester) lives in the layer; the CLI
only adapts argv → kwargs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


try:  # pragma: no cover
    import typer

    _HAS_TYPER = True
except ImportError:  # pragma: no cover
    typer = None  # type: ignore[assignment]
    _HAS_TYPER = False


def register_subcommands(parent: Any) -> None:
    """Mount the `predict` Typer subgroup onto `parent`.

    `parent` is a Typer instance from Phase 1's CLI. If typer is not
    installed (no CLI extras), this is a no-op.
    """
    if not _HAS_TYPER:
        return

    subgroup = typer.Typer(
        name="predict",
        help="Phase 3 — Predictive Apex commands.",
        no_args_is_help=True,
    )

    @subgroup.command("run")
    def run_cmd(
        trend_id: str = typer.Option(..., help="Trend id (UUID or stable string)."),
        tenant_id: str = typer.Option("default", help="Phase 1 RLS tenant."),
        horizon: int = typer.Option(24, help="Primary forecasting horizon (h)."),
        signals_file: Path | None = typer.Option(
            None,
            help="Path to JSON file with a list of signal dicts. "
            "If omitted, reads recent signals from Phase 1 DB.",
        ),
        out_format: str = typer.Option(
            "human",
            help="human | json — output formatter.",
        ),
    ) -> None:
        """Run inference for one trend."""
        asyncio.run(_run(trend_id, tenant_id, horizon, signals_file, out_format))

    @subgroup.command("serve")
    def serve_cmd(
        host: str = typer.Option("0.0.0.0", help="Bind host."),
        port: int = typer.Option(8000, help="Bind port."),
        workers: int = typer.Option(1, help="Number of worker processes."),
        reload: bool = typer.Option(False, help="Auto-reload on code change."),
    ) -> None:
        """Start the FastAPI inference service."""
        try:
            import uvicorn
        except ImportError as exc:
            typer.echo(f"uvicorn missing: {exc}", err=True)
            raise typer.Exit(1) from exc
        uvicorn.run(
            "aegis.predict.serving.app:create_app",
            factory=True,
            host=host,
            port=port,
            workers=workers,
            reload=reload,
            log_config=None,
        )

    @subgroup.command("bench")
    def bench_cmd(
        n_iters: int = typer.Option(50, help="Number of synthetic predictions."),
        n_signals: int = typer.Option(72, help="Signals per trend."),
    ) -> None:
        """Benchmark inference latency on synthetic signals.

        Produces p50/p90/p99 latency over `n_iters` runs. No DB needed.
        """
        asyncio.run(_bench(n_iters, n_signals))

    @subgroup.command("eval")
    def eval_cmd(
        samples_file: Path = typer.Argument(
            ..., help="JSON file: list of LabelledSample-shaped dicts."
        ),
        horizon: int = typer.Option(24, help="Forecasting horizon."),
        train_days: int = typer.Option(60, help="Train window length (days)."),
        out_file: Path | None = typer.Option(None, help="Where to write the JSON results."),
    ) -> None:
        """Run a walk-forward backtest from an offline samples file."""
        asyncio.run(_eval(samples_file, horizon, train_days, out_file))

    parent.add_typer(subgroup, name="predict")


async def _run(
    trend_id: str,
    tenant_id: str,
    horizon: int,
    signals_file: Path | None,
    out_format: str,
) -> None:
    from aegis.predict.inference import InferenceConfig, InferenceRunner

    signals: list[dict[str, Any]] | None = None
    if signals_file is not None:
        with signals_file.open("r", encoding="utf-8") as f:
            signals = json.load(f)
        # Parse ISO datetime strings.
        for r in signals:
            if isinstance(r.get("captured_at"), str):
                r["captured_at"] = datetime.fromisoformat(r["captured_at"])
    else:
        # No file supplied — try to pull recent signals from Phase 1 DB.
        try:
            import uuid as _uuid

            from aegis.config import settings
            from aegis.db.pool import PgPool
            from aegis.db.signals import fetch_recent_signals

            cfg_s = settings()
            pool = PgPool(dsn=cfg_s.pg_dsn_str)
            await pool.start()
            try:
                tenant_uuid = _uuid.UUID(cfg_s.default_tenant_id)
                db_rows = await fetch_recent_signals(pool, tenant_id=tenant_uuid, limit=200)
                signals = [dict(r) for r in db_rows]
            finally:
                await pool.close()
            if not signals:
                typer.echo(
                    "No signals in DB and no --signals-file provided. "
                    "Run `aegis scrape` first or pass --signals-file.",
                    err=True,
                )
                return
        except Exception as exc:
            typer.echo(
                f"Could not fetch signals from DB ({exc}). " "Pass --signals-file to run offline.",
                err=True,
            )
            return

    cfg = InferenceConfig(horizons=(1, 6, horizon, 72))
    runner = InferenceRunner(config=cfg)
    result = await runner.run(tenant_id=tenant_id, trend_id=trend_id, signals=signals)

    if out_format == "json":
        payload = {
            "bundle": result.bundle.model_dump(mode="json"),
            "halt_reasons": list(result.halt_reasons),
            "duration_ms": result.duration_ms,
            "graph_summary": result.graph_summary,
            "causal": [
                {"feature": a.feature, "contribution": a.contribution, "method": a.method}
                for a in result.causal
            ],
        }
        sys.stdout.write(json.dumps(payload, indent=2, default=str))
        sys.stdout.write("\n")
        return

    # Human format.
    primary = result.bundle.by_horizon(horizon) or result.bundle.predictions[0]
    lines = [
        f"trend_id        : {trend_id}",
        f"horizon         : {primary.horizon_hours}h",
        f"stage           : {primary.stage.value}",
        f"action          : {primary.action.value}",
        f"p_breakout      : {primary.p_breakout:.3f}",
        f"p_peak          : {primary.p_peak:.3f}",
        f"p_decline       : {primary.p_decline:.3f}",
        f"velocity_p10/p50/p90: {primary.velocity_p10:.3f} / {primary.velocity_p50:.3f} / {primary.velocity_p90:.3f}",
        f"confidence      : {primary.confidence:.3f}",
        f"halt_reasons    : {', '.join(result.halt_reasons) or '(none)'}",
        f"duration_ms     : {result.duration_ms:.2f}",
        f"is_heuristic    : {result.bundle.is_heuristic_only}",
    ]
    if result.causal:
        lines.append("causal_top:")
        for a in result.causal[:3]:
            lines.append(f"    {a.feature:30s} {a.contribution:+.3f}  [{a.method}]")
    sys.stdout.write("\n".join(lines) + "\n")


async def _bench(n_iters: int, n_signals: int) -> None:
    from aegis.predict.inference import InferenceRunner

    runner = InferenceRunner()
    base = datetime.now(UTC)
    signals = [
        {
            "id": f"sig-{i}",
            "platform": "twitter",
            "captured_at": base.replace(microsecond=0).fromtimestamp(
                base.timestamp() - (n_signals - i) * 3600, tz=UTC
            ),
            "title": None,
            "body": f"msg {i}",
            "url": None,
            "content_hash": f"hash{i}",
            "author_id": f"a{i % 5}",
            "views": i * 10,
            "likes": i,
            "comments": i // 2,
            "shares": 0,
            "saves": 0,
            "sentiment": 0.0,
            "commercial_intent": 0.0,
            "novelty": 0.5,
        }
        for i in range(n_signals)
    ]

    # Warm up — first call loads predictors.
    await runner.run(tenant_id="bench", trend_id="warmup", signals=signals)

    latencies: list[float] = []
    for i in range(n_iters):
        t0 = time.monotonic()
        await runner.run(tenant_id="bench", trend_id=f"trend-{i}", signals=signals)
        latencies.append((time.monotonic() - t0) * 1000.0)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p90 = latencies[int(len(latencies) * 0.90)]
    p99 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))]
    sys.stdout.write(f"iters={n_iters}  p50={p50:.2f}ms  p90={p90:.2f}ms  p99={p99:.2f}ms\n")


async def _eval(
    samples_file: Path,
    horizon: int,
    train_days: int,
    out_file: Path | None,
) -> None:
    from aegis.predict.backtest import BacktestSpec, WalkForwardBacktester
    from aegis.predict.backtest.runner import _Sample
    from aegis.predict.models.factory import load_model
    from aegis.predict.schemas import FeatureWindow

    with samples_file.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    samples: list[_Sample] = []
    for row in raw:
        ts = row["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        window = FeatureWindow.model_validate(row["window"])
        samples.append(
            _Sample(
                timestamp=ts,
                window=window,
                truth=row["truth"],
            )
        )

    model = load_model("heuristic_temporal")

    async def predict_fn(window: FeatureWindow) -> Any:
        bundle = await model.predict(window)
        return bundle.by_horizon(horizon) or bundle.predictions[0]

    spec = BacktestSpec(horizon=horizon, train_days=train_days)
    backtester = WalkForwardBacktester(spec=spec)
    folds, agg = await backtester.run(samples, predict_fn, model_id="heuristic_temporal-cli")

    payload = {
        "n_folds": agg.n_folds,
        "n_test_total": agg.n_test_total,
        "accuracy": agg.accuracy,
        "macro_f1": agg.macro_f1,
        "breakout_precision": agg.breakout_precision,
        "breakout_recall": agg.breakout_recall,
        "mae_log_velocity": agg.mae_log_velocity,
        "coverage_90": agg.coverage_90,
        "ece": agg.ece,
        "brier_breakout": agg.brier_breakout,
        "folds": [f.model_dump(mode="json") for f in folds],
    }
    text = json.dumps(payload, indent=2, default=str)
    if out_file:
        out_file.write_text(text + "\n", encoding="utf-8")
        sys.stdout.write(f"wrote {out_file}\n")
    else:
        sys.stdout.write(text + "\n")


def _typer_app_or_die() -> None:
    """Entry point used by `aegis-predict` console script.

    Builds a standalone Typer app that hosts the same subcommands the
    main `aegis` CLI exposes via `register_subcommands`. Lets users
    install just Phase 3 and still drive it from the shell without
    pulling Phase 1's CLI surface.
    """
    if not _HAS_TYPER:
        sys.stderr.write("typer is not installed — `pip install 'aegis-pulse-predict[dev]'`\n")
        sys.exit(1)
    app = typer.Typer(
        name="aegis-predict",
        help="AEGIS Pulse — Phase 3 Predictive Apex CLI.",
        no_args_is_help=True,
    )
    register_subcommands(app)
    app()
