"""
aegis.trust.calibration — pure calibration mathematics (Phase B)
================================================================

Deterministic functions over ``(p, y)`` pairs where ``p`` is a predicted
probability in [0, 1] and ``y`` is a binary realized outcome in {0, 1}.

NO predictive models, NO neural networks. Isotonic / Platt are calibration
*transforms* (monotone lookup maps), not predictors.

Doctrine (Evidence > Confidence): :func:`calibration_report` refuses to emit
ECE/Brier-skill numbers when the input lacks the variance that makes them
meaningful. A stream of identical confidences (e.g. Phase A's constant-0.5
backfill) returns ``status="insufficient_variance"`` — never a fake number.
This is the guard that would have automatically caught Phase A's 0.547 illusion.
"""

from __future__ import annotations

from aegis.trust.schemas import CalibrationReport, ReliabilityBin

# Below this many outcomes, a report is informational only.
MIN_OUTCOMES_FOR_REPORT = 30
# Distinct-confidence threshold below which calibration is meaningless.
MIN_DISTINCT_CONFIDENCES = 3
DEFAULT_BINS = 10


def _validate_pairs(ps: list[float], ys: list[float]) -> list[tuple[float, float]]:
    if len(ps) != len(ys):
        raise ValueError("p and y must have equal length")
    pairs: list[tuple[float, float]] = []
    for p, y in zip(ps, ys, strict=True):
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"p out of [0,1]: {p}")
        if y not in (0, 1, 0.0, 1.0):
            raise ValueError(f"y must be binary 0/1: {y}")
        pairs.append((float(p), float(y)))
    return pairs


def brier_score(ps: list[float], ys: list[float]) -> float:
    """Mean squared error of probabilistic predictions. Lower is better."""
    pairs = _validate_pairs(ps, ys)
    if not pairs:
        return 0.0
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def brier_skill_score(ps: list[float], ys: list[float]) -> float:
    """
    Brier skill vs the base-rate predictor (always predict mean(y)).

    1.0 = perfect, 0.0 = no better than the base rate, <0 = worse. A CONSTANT
    predictor scores exactly 0.0 here — this is the number that exposes Phase A's
    zero-lift backfill for what it is.
    """
    pairs = _validate_pairs(ps, ys)
    if not pairs:
        return 0.0
    base_rate = sum(y for _, y in pairs) / len(pairs)
    bs_ref = sum((base_rate - y) ** 2 for _, y in pairs) / len(pairs)
    if bs_ref == 0:  # base rate 0 or 1 — undefined skill
        return 0.0
    bs = brier_score(ps, ys)
    return 1.0 - bs / bs_ref


def reliability_bins(
    ps: list[float], ys: list[float], *, n_bins: int = DEFAULT_BINS
) -> list[ReliabilityBin]:
    """Equal-width [0,1] bins; empty bins are omitted."""
    pairs = _validate_pairs(ps, ys)
    bins: list[ReliabilityBin] = []
    width = 1.0 / n_bins
    for b in range(n_bins):
        lo = b * width
        hi = (b + 1) * width if b < n_bins - 1 else 1.0 + 1e-9
        members = [(p, y) for p, y in pairs if lo <= p < hi]
        if not members:
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        obs = sum(y for _, y in members) / len(members)
        bins.append(
            ReliabilityBin(
                lower=lo,
                upper=min(hi, 1.0),
                count=len(members),
                mean_predicted=mean_p,
                observed_freq=obs,
                gap=abs(mean_p - obs),
            )
        )
    return bins


def ece(ps: list[float], ys: list[float], *, n_bins: int = DEFAULT_BINS) -> float:
    """Expected Calibration Error — weighted mean of per-bin gaps."""
    pairs = _validate_pairs(ps, ys)
    if not pairs:
        return 0.0
    n = len(pairs)
    return sum((b.count / n) * b.gap for b in reliability_bins(ps, ys, n_bins=n_bins))


def mce(ps: list[float], ys: list[float], *, n_bins: int = DEFAULT_BINS) -> float:
    """Maximum Calibration Error — the worst-bin gap."""
    bins = reliability_bins(ps, ys, n_bins=n_bins)
    return max((b.gap for b in bins), default=0.0)


def isotonic_fit(ps: list[float], ys: list[float]) -> list[tuple[float, float]]:
    """
    Fit a monotone non-decreasing calibration map via Pool-Adjacent-Violators.

    Returns knots ``[(p, calibrated_p), ...]`` sorted by p. Apply with
    :func:`isotonic_apply` (piecewise-constant / linear interpolation). This is a
    calibration TRANSFORM, not a model — it adds zero parameters to any predictor.
    """
    pairs = sorted(_validate_pairs(ps, ys), key=lambda t: t[0])
    if not pairs:
        return []
    # PAVA on the y values ordered by p.
    xs = [p for p, _ in pairs]
    blocks: list[list[float]] = [[y] for _, y in pairs]
    block_x: list[list[float]] = [[x] for x in xs]
    i = 0
    while i < len(blocks) - 1:
        mean_i = sum(blocks[i]) / len(blocks[i])
        mean_next = sum(blocks[i + 1]) / len(blocks[i + 1])
        if mean_i > mean_next:  # violation — merge
            blocks[i].extend(blocks[i + 1])
            block_x[i].extend(block_x[i + 1])
            del blocks[i + 1]
            del block_x[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    knots: list[tuple[float, float]] = []
    for ys_block, xs_block in zip(blocks, block_x, strict=True):
        cal = sum(ys_block) / len(ys_block)
        # Anchor the block's calibrated value at its mean x.
        knots.append((sum(xs_block) / len(xs_block), cal))
    return knots


def isotonic_apply(knots: list[tuple[float, float]], p: float) -> float:
    """Apply an isotonic map (linear interpolation between knots)."""
    if not knots:
        return p
    if p <= knots[0][0]:
        return knots[0][1]
    if p >= knots[-1][0]:
        return knots[-1][1]
    from itertools import pairwise

    for (x0, y0), (x1, y1) in pairwise(knots):
        if x0 <= p <= x1:
            if x1 == x0:
                return y0
            t = (p - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return p


def calibration_report(
    ps: list[float],
    ys: list[float],
    *,
    entity_kind: str = "model",
    entity_id: str = "global",
    n_bins: int = DEFAULT_BINS,
) -> CalibrationReport:
    """
    Full calibration report with the mandatory degenerate-data guard.

    Returns ``status="insufficient_data"`` (<MIN_OUTCOMES_FOR_REPORT) or
    ``status="insufficient_variance"`` (fewer than MIN_DISTINCT_CONFIDENCES
    distinct p values) instead of fabricating ECE/Brier numbers.
    """
    pairs = _validate_pairs(ps, ys)
    n = len(pairs)
    base_rate = sum(y for _, y in pairs) / n if n else None

    if n < MIN_OUTCOMES_FOR_REPORT:
        return CalibrationReport(
            entity_kind=entity_kind, entity_id=entity_id, n=n,
            status="insufficient_data", base_rate=base_rate,
            notes=f"need >= {MIN_OUTCOMES_FOR_REPORT} outcomes, have {n}",
        )

    distinct = len({round(p, 4) for p, _ in pairs})
    if distinct < MIN_DISTINCT_CONFIDENCES:
        return CalibrationReport(
            entity_kind=entity_kind, entity_id=entity_id, n=n,
            status="insufficient_variance", base_rate=base_rate,
            notes=(
                f"only {distinct} distinct confidence value(s); calibration "
                "is meaningless over a (near-)constant predictor"
            ),
        )

    return CalibrationReport(
        entity_kind=entity_kind,
        entity_id=entity_id,
        n=n,
        status="ok",
        ece=ece(ps, ys, n_bins=n_bins),
        mce=mce(ps, ys, n_bins=n_bins),
        brier=brier_score(ps, ys),
        brier_skill_score=brier_skill_score(ps, ys),
        base_rate=base_rate,
        bins=reliability_bins(ps, ys, n_bins=n_bins),
    )
