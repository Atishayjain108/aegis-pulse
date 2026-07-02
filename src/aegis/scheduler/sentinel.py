"""AEGIS Market Sentinel — autonomous opportunity discovery.

The Sentinel is the piece that lets AEGIS investigate the market *without being
asked*. On each cycle it:

  1. Sweeps the keyless global-radar adapters (GDELT world news, Wikimedia
     pageview demand, multi-region Google Trends) — broad, every-continent
     ambient signal.
  2. Runs the real-time ``PatternEngine`` over that signal to find velocity
     **breakouts** — clusters that are accelerating, not just noise.
  3. Synthesizes a topic string from each breakout cluster's label.
  4. For each top breakout, runs the ``ProductIntelligenceEngine`` — which
     auto-routes to the relevant platform adapters and produces a full,
     numeric, gated market report. No human typed the query.
  5. Publishes each report to the ``aegis:sentinel:reports`` Redis stream and
     returns a summary.

Every stage is best-effort: a failure in one breakout never aborts the scan,
and the whole scan never raises. This mirrors the other autonomous jobs.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from aegis.scrape.base import ScrapeContext

_log = structlog.get_logger("aegis.scheduler.sentinel")

SENTINEL_STREAM = "aegis:sentinel:reports"
SENTINEL_STREAM_MAXLEN = 500

# Radar adapters swept every cycle — keyless, global coverage.
_RADAR: list[tuple[str, str, str]] = [
    ("aegis.scrape.sources.gdelt", "GdeltAdapter", "GdeltConfig"),
    ("aegis.scrape.sources.wikimedia", "WikimediaAdapter", "WikimediaConfig"),
    ("aegis.scrape.sources.google_trends_global",
     "GoogleTrendsGlobalAdapter", "GoogleTrendsGlobalConfig"),
]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class SentinelConfig:
    """Tunable thresholds (all overridable via ``AEGIS_SENTINEL_*`` env vars)."""

    radar_limit: int = field(default_factory=lambda: _env_int("AEGIS_SENTINEL_RADAR_LIMIT", 60))
    min_slope: float = field(default_factory=lambda: _env_float("AEGIS_SENTINEL_MIN_SLOPE", 1.5))
    min_confidence: float = field(
        default_factory=lambda: _env_float("AEGIS_SENTINEL_MIN_CONFIDENCE", 0.45))
    top_k: int = field(default_factory=lambda: _env_int("AEGIS_SENTINEL_TOP_K", 3))
    require_breakout: bool = field(
        default_factory=lambda: os.getenv("AEGIS_SENTINEL_REQUIRE_BREAKOUT", "1") != "0")
    report_depth: str = field(
        default_factory=lambda: os.getenv("AEGIS_SENTINEL_DEPTH", "standard"))


@dataclass(slots=True)
class SentinelScan:
    """Outcome of one Sentinel cycle."""

    started_at: datetime
    radar_signals: int = 0
    patterns_found: int = 0
    breakouts: int = 0
    reports: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "radar_signals": self.radar_signals,
            "patterns_found": self.patterns_found,
            "breakouts": self.breakouts,
            "report_count": len(self.reports),
            "reports": self.reports,
            "errors": self.errors[:10],
        }


class MarketSentinel:
    """Autonomous market scanner. Construct once, call :meth:`scan` per cycle."""

    def __init__(
        self,
        *,
        pool: Any | None = None,
        redis: Any | None = None,
        config: SentinelConfig | None = None,
    ) -> None:
        self._pool = pool
        self._redis = redis
        self._cfg = config or SentinelConfig()

    async def scan(self) -> SentinelScan:
        scan = SentinelScan(started_at=datetime.now(UTC))
        try:
            signals = await self._sweep_radar(scan)
            scan.radar_signals = len(signals)
            if not signals:
                _log.info("sentinel.no_radar_signals")
                return scan

            patterns = self._detect_breakouts(signals)
            scan.patterns_found = len(patterns)
            breakouts = self._rank_breakouts(patterns)
            scan.breakouts = len(breakouts)
            if not breakouts:
                _log.info("sentinel.no_breakouts", patterns=len(patterns))
                return scan

            for pat in breakouts[: self._cfg.top_k]:
                report = await self._investigate(pat, scan)
                if report is not None:
                    scan.reports.append(report)
                    await self._publish(report)

            _log.info(
                "sentinel.scan.done",
                radar_signals=scan.radar_signals,
                breakouts=scan.breakouts,
                reports=len(scan.reports),
            )
        except Exception as exc:  # never raise from the autonomous loop
            _log.warning("sentinel.scan.error", error=str(exc)[:200])
            scan.errors.append(str(exc)[:200])
        return scan

    # ------------------------------------------------------------------
    # 1. Radar sweep
    # ------------------------------------------------------------------
    async def _sweep_radar(self, scan: SentinelScan) -> list[dict[str, Any]]:
        import importlib

        ctx = ScrapeContext()
        out: list[dict[str, Any]] = []
        for module_path, cls_name, cfg_name in _RADAR:
            try:
                mod = importlib.import_module(module_path)
                adapter_cls = getattr(mod, cls_name)
                cfg_cls = getattr(mod, cfg_name)
                adapter = adapter_cls(cfg_cls())
                await adapter.setup(ctx)
                try:
                    async for raw in adapter.fetch_raw(ctx, limit=self._cfg.radar_limit):
                        sig = adapter.parse(raw, ctx)
                        if sig is not None:
                            out.append(self._signal_to_dict(sig))
                finally:
                    await adapter.teardown(ctx)
            except Exception as exc:
                msg = f"{cls_name}: {str(exc)[:120]}"
                _log.warning("sentinel.radar.adapter_error", adapter=cls_name, error=str(exc)[:120])
                scan.errors.append(msg)
        return out

    @staticmethod
    def _signal_to_dict(sig: Any) -> dict[str, Any]:
        ps = getattr(sig, "platform_specific", {}) or {}
        posted = getattr(sig, "posted_at", None)
        plat = getattr(sig, "platform", "")
        return {
            "title": getattr(sig, "title", "") or "",
            "platform": getattr(plat, "value", str(plat)),
            "url": str(getattr(sig, "url", "") or ""),
            "posted_at": posted.isoformat() if posted else None,
            "views": ps.get("views") or ps.get("approx_traffic") or 0,
            "source_country": ps.get("source_country") or ps.get("geo") or "",
        }

    # ------------------------------------------------------------------
    # 2 + 3. Breakout detection + ranking
    # ------------------------------------------------------------------
    def _detect_breakouts(self, signals: list[dict[str, Any]]) -> list[Any]:
        try:
            from aegis.scrape.pattern_engine import PatternEngine
        except Exception as exc:
            _log.warning("sentinel.pattern_engine.unavailable", error=str(exc)[:120])
            return []
        try:
            return PatternEngine().detect(signals)
        except Exception as exc:
            _log.warning("sentinel.detect.error", error=str(exc)[:120])
            return []

    def _rank_breakouts(self, patterns: list[Any]) -> list[Any]:
        cfg = self._cfg
        kept = []
        for p in patterns:
            label = (getattr(p, "label", "") or "").strip()
            if not label or len(label) < 3:
                continue
            if cfg.require_breakout and not getattr(p, "is_breakout", False):
                continue
            if getattr(p, "velocity_slope", 0.0) < cfg.min_slope:
                continue
            if getattr(p, "confidence", 0.0) < cfg.min_confidence:
                continue
            kept.append(p)
        # Strongest acceleration first, then confidence.
        kept.sort(
            key=lambda p: (getattr(p, "acceleration", 0.0), getattr(p, "confidence", 0.0)),
            reverse=True,
        )
        return kept

    # ------------------------------------------------------------------
    # 4. Investigate a breakout → full market report
    # ------------------------------------------------------------------
    async def _investigate(self, pattern: Any, scan: SentinelScan) -> dict[str, Any] | None:
        topic = (getattr(pattern, "label", "") or "").strip()
        if not topic:
            return None
        try:
            from aegis.intelligence.product_intel import ProductIntelligenceEngine

            engine = ProductIntelligenceEngine(pool=self._pool, redis=self._redis)
            report = await engine.analyze(
                topic,
                depth=self._cfg.report_depth,
                use_llm=True,
                gate=True,
            )
            payload = report.to_dict()
            payload["discovered_by"] = "sentinel"
            payload["trigger"] = {
                "label": topic,
                "pattern_type": getattr(pattern, "pattern_type", ""),
                "velocity_slope": round(getattr(pattern, "velocity_slope", 0.0), 3),
                "acceleration": round(getattr(pattern, "acceleration", 0.0), 3),
                "confidence": round(getattr(pattern, "confidence", 0.0), 3),
                "platforms": getattr(pattern, "platforms", []),
            }
            _log.info(
                "sentinel.report",
                topic=topic,
                products=payload.get("product_count", 0),
                platforms=len(payload.get("platforms", []) or []),
            )
            return payload
        except Exception as exc:
            _log.warning("sentinel.investigate.error", topic=topic, error=str(exc)[:160])
            scan.errors.append(f"investigate[{topic}]: {str(exc)[:120]}")
            return None

    # ------------------------------------------------------------------
    # 5. Publish
    # ------------------------------------------------------------------
    async def _publish(self, report: dict[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.xadd(
                SENTINEL_STREAM,
                {"body": json.dumps(report, default=str)},
                maxlen=SENTINEL_STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:
            _log.warning("sentinel.publish.error", error=str(exc)[:120])


__all__ = [
    "SENTINEL_STREAM",
    "MarketSentinel",
    "SentinelConfig",
    "SentinelScan",
]
