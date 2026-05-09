"""Author database operations.

Two public functions:
- ``upsert_author``: insert or update a single Author row, returning its UUID.
- ``fetch_author``: look up an author by (platform, platform_user_id).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from aegis.db.pool import PgPool
    from aegis.schemas.enums import Platform
    from aegis.schemas.signal import Author

_UPSERT_SQL = """
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
"""


async def upsert_author(
    pool: PgPool,
    author: Author,
    *,
    platform: Platform,
    tenant_id: UUID,
) -> UUID | None:
    """Upsert a single author row. Returns the ``author_id`` UUID."""
    async with pool.acquire(tenant_id=tenant_id) as conn:
        row = await conn.fetchrow(
            _UPSERT_SQL,
            tenant_id,
            platform.value,
            author.platform_user_id,
            author.handle,
            author.display_name,
            author.verified,
            author.account_created_at,
            str(author.profile_url) if author.profile_url else None,
            author.bio_text,
            author.follower_count,
            author.following_count,
            author.total_posts,
        )
    return row["author_id"] if row else None


async def fetch_author(
    pool: PgPool,
    *,
    platform: Platform,
    platform_user_id: str,
    tenant_id: UUID,
) -> dict[str, Any] | None:
    """Return the author row as a dict, or ``None`` if not found."""
    row = await pool.fetchrow(
        """
        SELECT * FROM authors
        WHERE platform = $1::platform_enum AND platform_user_id = $2
        """,
        platform.value,
        platform_user_id,
        tenant_id=tenant_id,
    )
    return dict(row) if row else None


__all__ = ["fetch_author", "upsert_author"]
