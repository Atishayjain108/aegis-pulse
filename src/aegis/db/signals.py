"""Signal database operations.

Two public functions:
- ``insert_signals``: upsert authors then batch-insert ProductSignals.
- ``fetch_recent_signals``: return the N most recent signals for a tenant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from aegis.db.pool import PgPool
    from aegis.schemas.signal import ProductSignal


async def insert_signals(
    pool: PgPool,
    signals: list[ProductSignal],
    *,
    tenant_id: UUID,
) -> int:
    """Upsert authors, then insert signals. Returns count of new rows written."""
    if not signals:
        return 0

    inserted = 0
    async with pool.acquire(tenant_id=tenant_id) as conn:
        for signal in signals:
            author_id: UUID | None = None
            if signal.author is not None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO authors (
                        tenant_id, platform, platform_user_id, handle, display_name,
                        verified, account_created_at, profile_url, bio_text,
                        follower_count, following_count, total_posts
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (platform, platform_user_id) DO UPDATE SET
                        last_seen_at    = NOW(),
                        handle          = COALESCE(EXCLUDED.handle,          authors.handle),
                        display_name    = COALESCE(EXCLUDED.display_name,    authors.display_name),
                        follower_count  = COALESCE(EXCLUDED.follower_count,  authors.follower_count),
                        following_count = COALESCE(EXCLUDED.following_count, authors.following_count),
                        total_posts     = COALESCE(EXCLUDED.total_posts,     authors.total_posts),
                        verified        = COALESCE(EXCLUDED.verified,        authors.verified),
                        updated_at      = NOW()
                    RETURNING author_id
                    """,
                    tenant_id,
                    signal.platform.value,
                    signal.author.platform_user_id,
                    signal.author.handle,
                    signal.author.display_name,
                    signal.author.verified,
                    signal.author.account_created_at,
                    str(signal.author.profile_url) if signal.author.profile_url else None,
                    signal.author.bio_text,
                    signal.author.follower_count,
                    signal.author.following_count,
                    signal.author.total_posts,
                )
                if row:
                    author_id = row["author_id"]

            ts = signal.posted_at if signal.posted_at is not None else signal.provenance.scraped_at
            scraped_at = signal.provenance.scraped_at
            eng = signal.engagement
            prov = signal.provenance
            conf = signal.confidence
            price = signal.price

            result = await conn.execute(
                """
                INSERT INTO signals (
                    signal_id, tenant_id, schema_version, platform, tier, external_id, url, ts,
                    posted_at, scraped_at, title, raw_text, pii_scrubbed_text, language,
                    modality, tags, intent, author_id,
                    views, likes, comments, shares, saves, watch_time_seconds, reactions,
                    price_amount, price_currency, price_original, price_on_sale,
                    scrape_method, scraper_version, proxy_id, user_agent, ja3_fingerprint,
                    tos_risk, rate_limit_hit, captcha_encountered,
                    completeness, source_confidence, freshness_seconds,
                    content_hash, platform_specific
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                    $19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,
                    $30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42
                )
                ON CONFLICT (platform, external_id, ts) DO NOTHING
                """,
                signal.signal_id,
                tenant_id,
                signal.schema_version,
                signal.platform.value,
                signal.tier.value,
                signal.external_id,
                str(signal.url) if signal.url else None,
                ts,
                signal.posted_at,
                scraped_at,
                signal.title,
                signal.raw_text,
                signal.pii_scrubbed_text,
                signal.language,
                signal.modality.value,
                list(signal.tags),
                signal.intent.value,
                author_id,
                eng.views if eng else None,
                eng.likes if eng else None,
                eng.comments if eng else None,
                eng.shares if eng else None,
                eng.saves if eng else None,
                eng.watch_time_seconds if eng else None,
                eng.reactions if eng else None,
                # Price columns — required for T2_commerce by the
                # signals_commerce_needs_price CHECK constraint. Previously the
                # price lived only in platform_specific JSONB, so every commerce
                # signal (eBay/BestBuy/Etsy, all T2) failed to insert.
                price.amount if price else None,
                price.currency if price else None,
                price.original_amount if price else None,
                price.is_on_sale if price else None,
                prov.method.value,
                prov.scraper_version,
                prov.proxy_id,
                prov.user_agent,
                prov.ja3_fingerprint,
                prov.tos_risk.value,
                prov.rate_limit_hit,
                prov.captcha_encountered,
                conf.completeness,
                conf.source_confidence,
                conf.freshness_seconds,
                signal.content_hash,
                signal.platform_specific,
            )
            # asyncpg returns "INSERT 0 1" for a new row, "INSERT 0 0" for DO NOTHING.
            if result.endswith(" 1"):
                inserted += 1

    return inserted


async def fetch_recent_signals(
    pool: PgPool,
    *,
    tenant_id: UUID,
    limit: int,
    platform: str | None = None,
    since: Any | None = None,
) -> list[dict[str, Any]]:
    """Return the N most recent signals for a tenant, newest first.

    Args:
        pool: shared asyncpg pool.
        tenant_id: RLS-scoping tenant.
        limit: max rows to return.
        platform: optional platform filter (string, matched against platform::text).
        since: optional lower-bound timestamp (rows with ts >= since only).
    """
    # Build WHERE clauses dynamically so we avoid scanning the full table
    # when a time-window constraint is provided (the ts index is used).
    conditions: list[str] = []
    params: list[Any] = [limit]

    if since is not None:
        params.append(since)
        conditions.append(f"ts >= ${len(params)}")

    if platform is not None:
        params.append(platform)
        conditions.append(f"platform::text = ${len(params)}")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await pool.fetch(
        f"""
        SELECT
            signal_id, platform, external_id, title, url, ts,
            scraped_at AS captured_at,
            intent, author_id, raw_text,
            views, likes, comments, shares, saves
        FROM signals
        {where_clause}
        ORDER BY ts DESC
        LIMIT $1
        """,
        *params,
        tenant_id=tenant_id,
    )
    return [dict(row) for row in rows]


__all__ = ["fetch_recent_signals", "insert_signals"]
