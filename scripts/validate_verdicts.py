#!/usr/bin/env python3
"""
validate_verdicts.py — offline, no-look-ahead scoring of AEGIS's deterministic core.

WHY THIS EXISTS
---------------
AEGIS prints a verdict + confidence for every trend, but those numbers have never
been scored against a real "did it actually sell / sustain / die" outcome. This
script is the smallest honest test of the question:

    "Is AEGIS right more often than a coin flip?"

It takes a CSV of historical trends whose outcomes are now KNOWN in hindsight,
feeds the interest curve *up to the peak only* (no look-ahead) into the same
`heuristic_predict` floor the live system uses, and reports accuracy, a 2x2
confusion matrix, Brier score, Brier skill vs. the base rate, and a calibration
table.

POSITIVE CLASS = "sustained" (a durable seller). Negatives = "fad" or "flat".

USAGE
-----
    # 1. Generate a template you fill in from Google Trends / Amazon rank history:
    python scripts/validate_verdicts.py --make-template labels.csv

    # 2. Score AEGIS against your filled-in labels:
    python scripts/validate_verdicts.py --score labels.csv
    python scripts/validate_verdicts.py --score labels.csv --threshold 0.45

CSV FORMAT  (one row per product)
---------------------------------
    query,outcome,series
    air fryer,sustained,2 3 3 5 8 14 22 35 55 70
    fidget spinner,fad,1 1 2 8 40 95 60
    garlic press,flat,30 28 31 29 30 30 27 31

  * outcome ∈ {sustained, fad, flat}   (positive class = sustained)
  * series  = whitespace-separated interest values OLDEST→PEAK ONLY.
              Use the curve UP TO the peak — never include the decline,
              that is the look-ahead you are trying to avoid.
              Google Trends "Interest over time" (0-100) is ideal and free.

This script imports nothing AEGIS-specific except the prediction floor, so it
runs with no DB, no Redis, no network.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aegis.predict import FEATURE_DIM, FEATURE_NAMES
from aegis.predict.models.heuristic import heuristic_predict
from aegis.predict.schemas import FeatureWindow

POSITIVE = "sustained"
DEFAULT_THRESHOLD = 0.50  # P(breakout/sustain) >= threshold → predict "sustained"
SCORE_HORIZON_H = 72      # use the longest default horizon as the "will it last" read

# A starter set of PUBLICLY-KNOWN outcomes. Series are left BLANK on purpose —
# fill each from Google Trends so the data is real, not fabricated. The labels
# are retrospective public fact and safe to pre-seed.
TEMPLATE_ROWS = [
    ("air fryer", "sustained", ""),
    ("stanley cup tumbler", "sustained", ""),
    ("resistance bands", "sustained", ""),
    ("blue light glasses", "sustained", ""),
    ("reusable water bottle", "sustained", ""),
    ("fidget spinner", "fad", ""),
    ("fingerboard", "fad", ""),
    ("pop it toy", "fad", ""),
    ("plague inc merch", "fad", ""),
    ("hoverboard", "fad", ""),
    ("garlic press", "flat", ""),
    ("can opener", "flat", ""),
    ("shoelaces", "flat", ""),
    ("paper clips", "flat", ""),
    ("ice cube tray", "flat", ""),
]


@dataclass
class Row:
    query: str
    outcome: str
    series: list[float]


def _read_labels(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            series_str = (raw.get("series") or "").strip()
            if not series_str:
                continue  # unfilled template row — skip silently
            try:
                series = [float(x) for x in series_str.replace(",", " ").split()]
            except ValueError:
                print(f"  ! skipping '{raw.get('query')}': non-numeric series", file=sys.stderr)
                continue
            if len(series) < 3:
                print(f"  ! skipping '{raw.get('query')}': series too short (<3)", file=sys.stderr)
                continue
            outcome = (raw.get("outcome") or "").strip().lower()
            if outcome not in {"sustained", "fad", "flat"}:
                print(f"  ! skipping '{raw.get('query')}': bad outcome '{outcome}'", file=sys.stderr)
                continue
            rows.append(Row(query=raw["query"].strip(), outcome=outcome, series=series))
    return rows


def _window_from_series(query: str, series: list[float]) -> FeatureWindow:
    """Build a FeatureWindow from a 1-D interest curve (no look-ahead).

    Mapping (an honest proxy, documented so nobody mistakes it for ground truth):
      signal_count   = interest value per bucket
      unique_authors = 0.7 * interest  (diversity proxy; keeps breakout gate reachable)
      velocity_{1,6,24} = trailing log-growth of interest over 1 / 6 / 24 buckets
      sentiment/commercial/novelty = neutral (0.0) — this harness scores MOMENTUM,
        not the scout's commerce features, which need real marketplace data.
    """
    n = len(series)
    idx = {name: i for i, name in enumerate(FEATURE_NAMES)}
    log_c = [math.log1p(max(0.0, v)) for v in series]

    def vel(i: int, k: int) -> float:
        j = max(0, i - k)
        return log_c[i] - log_c[j]

    flat: list[float] = []
    for i in range(n):
        row = [0.0] * FEATURE_DIM
        row[idx["signal_count"]] = max(0.0, series[i])
        row[idx["unique_authors"]] = max(0.0, series[i] * 0.7)
        row[idx["platform_diversity"]] = 1.0
        row[idx["velocity_1h"]] = vel(i, 1)
        row[idx["velocity_6h"]] = vel(i, 6)
        row[idx["velocity_24h"]] = vel(i, 24)
        flat.extend(row)

    return FeatureWindow(
        trend_id=query[:64],
        window_size=n,
        feature_dim=FEATURE_DIM,
        feature_names=FEATURE_NAMES,
        values=flat,
        captured_at=datetime.now(UTC),
    )


def _predict_p_sustain(row: Row) -> float:
    """P(sustain) for one row = p_breakout at the longest horizon. No look-ahead."""
    window = _window_from_series(row.query, row.series)
    preds = heuristic_predict(window=window, graph=None, horizons=(24, SCORE_HORIZON_H))
    longest = max(preds, key=lambda p: p.horizon_hours)
    return float(longest.p_breakout)


def _brier(probs: list[float], labels: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, labels, strict=True)) / len(probs)


def _calibration(probs: list[float], labels: list[int]) -> list[tuple[str, int, float, float]]:
    bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    out: list[tuple[str, int, float, float]] = []
    for lo, hi in bins:
        members = [(p, y) for p, y in zip(probs, labels, strict=True) if lo <= p < hi]
        if not members:
            out.append((f"{lo:.1f}-{min(hi,1.0):.1f}", 0, 0.0, 0.0))
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        frac_pos = sum(y for _, y in members) / len(members)
        out.append((f"{lo:.1f}-{min(hi,1.0):.1f}", len(members), mean_p, frac_pos))
    return out


def _score(path: Path, threshold: float) -> int:
    rows = _read_labels(path)
    if not rows:
        print(f"No usable labeled rows in {path}. Fill in the 'series' column "
              f"from Google Trends and re-run.", file=sys.stderr)
        return 1

    probs: list[float] = []
    labels: list[int] = []
    per_row: list[tuple[Row, float, int, int]] = []  # row, p, pred, actual

    for r in rows:
        p = _predict_p_sustain(r)
        actual = 1 if r.outcome == POSITIVE else 0
        pred = 1 if p >= threshold else 0
        probs.append(p)
        labels.append(actual)
        per_row.append((r, p, pred, actual))

    n = len(rows)
    base_rate = sum(labels) / n
    tp = sum(1 for _, _, pr, ac in per_row if pr == 1 and ac == 1)
    fp = sum(1 for _, _, pr, ac in per_row if pr == 1 and ac == 0)
    tn = sum(1 for _, _, pr, ac in per_row if pr == 0 and ac == 0)
    fn = sum(1 for _, _, pr, ac in per_row if pr == 0 and ac == 1)
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    rec = tp / (tp + fn) if (tp + fn) else float("nan")

    brier = _brier(probs, labels)
    # Reference Brier: always predict the base rate.
    brier_ref = _brier([base_rate] * n, labels)
    skill = 1.0 - brier / brier_ref if brier_ref > 0 else float("nan")

    # Significance vs. a fair coin: need k correct out of n to clear ~p<0.05.
    # Two-sided binomial 0.5 null, rough normal approx threshold.
    need = math.ceil(n / 2 + 1.645 * math.sqrt(n) / 2)

    print("\n" + "=" * 64)
    print(f"AEGIS verdict validation — {n} labeled trends   (threshold={threshold:.2f})")
    print("=" * 64)
    print(f"Positive class = '{POSITIVE}'   base rate = {base_rate:.2%}")
    print("\nConfusion matrix (predicted x actual):")
    print("                 actual=sustain   actual=not")
    print(f"  pred=sustain        {tp:3d}            {fp:3d}")
    print(f"  pred=not            {fn:3d}            {tn:3d}")
    print(f"\n  accuracy   : {acc:.2%}   ({tp+tn}/{n} correct)")
    print(f"  precision  : {prec:.2%}   (of ENTER calls, how many sustained)")
    print(f"  recall     : {rec:.2%}")
    print(f"  Brier      : {brier:.4f}   (lower is better; 0.25 = clueless coin)")
    print(f"  Brier skill: {skill:+.3f}  vs base-rate (>0 means better than guessing)")
    print(f"\n  beats a coin flip at p<0.05 if accuracy ≥ {need}/{n} "
          f"({need/n:.0%}) → {'YES' if (tp+tn) >= need else 'NOT YET'}")

    print("\nCalibration (does confidence mean anything?):")
    print("  bucket      n   mean_p   actual_sustain_rate")
    for label, cnt, mean_p, frac in _calibration(probs, labels):
        if cnt == 0:
            continue
        print(f"  {label:<8} {cnt:3d}    {mean_p:.2f}      {frac:.2f}")

    print("\nPer-trend:")
    print("  P(sustain) pred      actual    query")
    for r, p, pr, ac in sorted(per_row, key=lambda t: -t[1]):
        mark = "ok " if pr == ac else "MISS"
        print(f"   {p:.2f}     {'ENTER' if pr else 'pass ':<5} {r.outcome:<9} {mark} {r.query}")

    print("\nHonest read:")
    if n < 30:
        print(f"  ⚠ Only {n} labels — too few to trust. Aim for ≥40 before acting on this.")
    if (tp + tn) < need:
        print("  ⚠ Not distinguishable from a coin flip. The MOMENTUM signal alone")
        print("    is not enough — a real demand input is needed before any threshold matters.")
    else:
        print("  ✓ Better than chance on this set. Now FIT the threshold to maximise")
        print("    Brier skill, and print the measured accuracy next to every live verdict.")
    print("=" * 64 + "\n")
    return 0


def _make_template(path: Path) -> int:
    if path.exists():
        print(f"Refusing to overwrite existing {path}", file=sys.stderr)
        return 1
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["query", "outcome", "series"])
        for q, o, s in TEMPLATE_ROWS:
            w.writerow([q, o, s])
    print(f"Wrote template → {path}")
    print("Next: open it, and for each row paste the Google Trends 'Interest over")
    print("time' values OLDEST→PEAK (whitespace-separated) into the 'series' column.")
    print("Stop at the peak — do not include the decline (that would be look-ahead).")
    print(f"Then: python {sys.argv[0]} --score {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--make-template", metavar="CSV", help="write a starter labels CSV to fill in")
    g.add_argument("--score", metavar="CSV", help="score AEGIS against a filled-in labels CSV")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"P(sustain) decision threshold (default {DEFAULT_THRESHOLD})")
    args = ap.parse_args()

    if args.make_template:
        return _make_template(Path(args.make_template))
    return _score(Path(args.score), args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
