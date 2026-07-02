"""
PROJECT OMEGA — confidence audit (read-only experiment).

Reconstructs heuristic predictions over historical anchors and tests, OUT OF
SAMPLE (k-fold isotonic), which probability formulation actually discriminates
the realized "signal_count rises" label. Drives the remediation plan with
evidence rather than assumption.
"""

from __future__ import annotations

import asyncio
import statistics as stats
from datetime import UTC, datetime, timedelta

import asyncpg

from aegis.predict.features.builder import build_window_from_rows
from aegis.predict.models.heuristic import heuristic_predict
from aegis.trust.calibration import brier_score, ece, isotonic_apply, isotonic_fit
from aegis.trust.claim_emitter import _row_to_builder_dict

DSN = "postgresql://aegis_app:aegis_app_dev_pw@localhost:5433/aegis"
TENANT = "00000000-0000-0000-0000-000000000001"
HORIZON = 72


async def collect() -> list[dict]:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    now = datetime.now(UTC)
    horizon = timedelta(hours=HORIZON)
    rows_out: list[dict] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config('app.current_tenant',$1,false)", TENANT)
        anchors = await conn.fetch(
            """
            SELECT tag AS trend_key, MIN(ts) AS claim_ts
            FROM signals, unnest(tags) AS tag
            WHERE ts >= $1 AND ts < $2
            GROUP BY tag, date_trunc('day', ts)
            HAVING COUNT(*) >= 3
            ORDER BY MIN(ts) LIMIT 1500
            """,
            now - timedelta(days=120), now - horizon,
        )
    for a in anchors:
        tk = a["trend_key"]
        claim_ts = a["claim_ts"]
        obs_end = claim_ts + horizon
        if obs_end > now:
            continue
        async with pool.acquire() as conn:
            await conn.execute("SELECT set_config('app.current_tenant',$1,false)", TENANT)
            base = await conn.fetchval(
                "SELECT COUNT(*) FROM signals WHERE $1=ANY(tags) AND ts>=$2 AND ts<$3",
                tk, claim_ts - horizon, claim_ts)
            obs = await conn.fetchval(
                "SELECT COUNT(*) FROM signals WHERE $1=ANY(tags) AND ts>=$2 AND ts<$3",
                tk, claim_ts, obs_end)
            wrows = await conn.fetch(
                """SELECT signal_id, platform, ts, title, pii_scrubbed_text, url,
                          external_id, author_id, views, likes, comments, shares,
                          saves, source_confidence, completeness
                   FROM signals WHERE $1=ANY(tags) AND ts>=$2 AND ts<$3 ORDER BY ts""",
                tk, claim_ts - horizon, claim_ts)
        if base < 3:
            continue
        window = build_window_from_rows(
            trend_id=tk, tenant_id=TENANT,
            rows=[_row_to_builder_dict(dict(r)) for r in wrows], window_end=claim_ts)
        try:
            preds = heuristic_predict(window=window, graph=None, horizons=(HORIZON,))
        except Exception:
            preds = []
        if not preds:
            continue
        p = preds[0]
        rel = (obs - base) / base if base else 0.0
        y = 1.0 if rel > 0.10 else 0.0
        rows_out.append({
            "p_breakout": float(p.p_breakout), "p_decline": float(p.p_decline),
            "p_peak": float(p.p_peak), "confidence": float(p.confidence),
            "stage": p.stage.value, "y": y, "base": base, "obs": obs,
        })
    await pool.close()
    return rows_out


def kfold_calibrated_bss(ps: list[float], ys: list[float], k: int = 5) -> tuple[float, float]:
    """Out-of-sample Brier skill after isotonic calibration (k-fold)."""
    n = len(ps)
    idx = list(range(n))
    base_rate = sum(ys) / n
    bs_ref = sum((base_rate - y) ** 2 for y in ys) / n
    oos_pred: list[float] = [0.0] * n
    for f in range(k):
        test = set(idx[f::k])
        tr_p = [ps[i] for i in idx if i not in test]
        tr_y = [ys[i] for i in idx if i not in test]
        knots = isotonic_fit(tr_p, tr_y)
        for i in test:
            oos_pred[i] = isotonic_apply(knots, ps[i])
    bs = sum((oos_pred[i] - ys[i]) ** 2 for i in range(n)) / n
    bss = 1.0 - bs / bs_ref if bs_ref else 0.0
    return bss, ece(oos_pred, ys)


def main() -> None:
    data = asyncio.run(collect())
    n = len(data)
    ys = [d["y"] for d in data]
    base_rate = sum(ys) / n
    print(f"n={n}  base_rate(rise)={base_rate:.3f}")
    print(f"stages: {stats.mode([d['stage'] for d in data])} (mode)")

    # Candidate probability formulations to audit.
    candidates = {
        "raw p_breakout": [d["p_breakout"] for d in data],
        "two-class p_breakout/(p_b+p_d)": [
            d["p_breakout"] / (d["p_breakout"] + d["p_decline"] + 1e-9) for d in data
        ],
        "velocity-free base_rate const": [base_rate for _ in data],
        "1 - p_decline": [1 - d["p_decline"] for d in data],
    }
    print("\n--- discrimination (corr sign) + OOS calibrated Brier skill ---")
    for name, ps in candidates.items():
        # discrimination: mean y for top half vs bottom half of p
        order = sorted(range(n), key=lambda i: ps[i])
        lo = [ys[i] for i in order[: n // 2]]
        hi = [ys[i] for i in order[n // 2 :]]
        disc = (sum(hi) / len(hi)) - (sum(lo) / len(lo)) if lo and hi else 0.0
        bss, e = kfold_calibrated_bss(ps, ys)
        print(f"  {name:38s} disc={disc:+.3f}  OOS_BSS={bss:+.4f}  OOS_ECE={e:.3f}")

    # raw Brier of the model's own confidence (the Phase B number)
    conf = [d["confidence"] for d in data]
    print(f"\nraw confidence Brier={brier_score(conf, ys):.3f} mean_conf={stats.mean(conf):.3f}")


if __name__ == "__main__":
    main()
