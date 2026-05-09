"""Canonical ``ProductSignal`` — the lingua franca of the entire AEGIS pipeline.

Every scraper produces ``ProductSignal`` instances. Every feature extractor,
agent, model, and storage layer consumes them. If a field is missing here,
it does not exist in the system.

Design constraints:

- **Forward-compatible**: adding a field is a minor version bump; renaming
  or removing one is a major. ``SCHEMA_VERSION`` is checked by consumers.
- **Strict by default**: unknown fields are rejected. Bad data is surfaced
  at ingestion, not discovered silently three weeks later during backtesting.
- **Timezone-aware everywhere**: every datetime is UTC. Ruff's ``DTZ`` rule +
  validator below enforces this.
- **Content-addressable**: ``content_hash`` is deterministic given
  ``(platform, external_id, url, raw_text)`` — enables idempotent writes.
- **PII-aware**: ``raw_text`` fields pass through ``pii_scrubbed`` before
  storage; see ``PII_SCRUB_PLACEHOLDER`` in ``constants``.

Author: AEGIS Pulse Team
Relationship: foundation — every scraper in ``scrape/``, every feature
extractor in ``features/``, and every DB write in ``db/`` touches this.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    NonNegativeFloat,
    NonNegativeInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from aegis.constants import (
    CROSS_MODAL_COHERENCE_MAX,
    CROSS_MODAL_COHERENCE_MIN,
    SCHEMA_VERSION,
    SIGNAL_CONTENT_HASH_ALGO,
    SIGNAL_CONTENT_HASH_DIGEST_BYTES,
)
from aegis.schemas.enums import (
    ContentModality,
    IntentType,
    Platform,
    ScrapeMethod,
    SourceTier,
    ToSRisk,
    platform_tier,
)

# =============================================================================
# Type aliases — reused constrained primitives
# =============================================================================

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
"""Short strings: titles, hashtags (joined), author handles, etc."""

LongText = Annotated[str, StringConstraints(max_length=20_000)]
"""Captions, comment bodies, descriptions. 20k is a safety cap — signals above
this are split or truncated upstream."""

TagString = Annotated[str, StringConstraints(
    strip_whitespace=True,
    to_lower=True,
    min_length=1,
    max_length=128,
    pattern=r"^[a-z0-9_\-\.]+$",
)]
"""Hashtag / keyword. Lowercased, limited charset for cross-platform dedup."""

Currency = Annotated[str, StringConstraints(
    strip_whitespace=True,
    to_upper=True,
    min_length=3,
    max_length=3,
    pattern=r"^[A-Z]{3}$",
)]
"""ISO 4217 currency code (USD, EUR, INR, ...). Upper-cased, exactly 3 chars."""


class _FrozenBase(BaseModel):
    """Common model config. Immutable, strict, aliases-allowed."""

    model_config = ConfigDict(
        extra="forbid",          # unknown fields → error (prevents silent schema drift)
        frozen=True,             # instances are immutable (safer for async + hashing)
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        # Serialize Decimals as strings so no precision loss over JSON.
        ser_json_bytes="base64",
    )


# =============================================================================
# Sub-objects
# =============================================================================


class Author(_FrozenBase):
    """Creator / account attribution for a signal.

    Fields follow a "best-effort" philosophy: we record what the source gives
    us and leave the rest ``None``. Downstream code must tolerate partial data.
    """

    platform_user_id: ShortText
    """Platform-native ID (never a display name — IDs are stable, handles change)."""

    handle: ShortText | None = None
    """Display handle at scrape time — may rename; do NOT use as a key."""

    display_name: ShortText | None = None
    follower_count: NonNegativeInt | None = None
    following_count: NonNegativeInt | None = None
    total_posts: NonNegativeInt | None = None

    verified: bool | None = None
    """Platform verification badge (blue check, creator mark, etc.)."""

    account_created_at: AwareDatetime | None = None
    """Account creation date. Young accounts + high engagement = bot suspect."""

    profile_url: HttpUrl | None = None
    bio_text: LongText | None = None


class EngagementMetrics(_FrozenBase):
    """Raw engagement counts at scrape time.

    All counts are snapshots — use velocity deltas (in ``features/velocity.py``)
    rather than raw numbers for trend scoring."""

    views: NonNegativeInt | None = None
    likes: NonNegativeInt | None = None
    comments: NonNegativeInt | None = None
    shares: NonNegativeInt | None = None
    saves: NonNegativeInt | None = None
    """Save rate is a high-alpha proxy for latent purchase intent (Pinterest, Reels)."""

    reactions: dict[str, NonNegativeInt] | None = None
    """Platform-specific reaction types, e.g. ``{"love": 42, "wow": 7}``."""

    watch_time_seconds: NonNegativeFloat | None = None
    """Video watch time — YouTube/TikTok proxy for quality of attention."""

    @property
    def total_engagements(self) -> int:
        """Sum of all quantifiable engagements. Used in bot/ratio features."""
        fields: tuple[int | None, ...] = (
            self.likes, self.comments, self.shares, self.saves,
        )
        return sum(v for v in fields if v is not None)

    @property
    def engagement_rate(self) -> float | None:
        """Engagement over views. Returns ``None`` when views are unavailable
        or zero — do NOT coerce to 0.0 (missing ≠ nonexistent)."""
        if self.views is None or self.views == 0:
            return None
        return self.total_engagements / self.views


class Price(_FrozenBase):
    """Commerce price snapshot. Only present on T2 (commerce) signals.

    Rationale for using ``Decimal``: floating-point arithmetic on money is
    a root cause of hard-to-diagnose margin errors. Decimal stringifies
    losslessly into Postgres ``numeric``."""

    amount: Decimal = Field(..., ge=Decimal("0"))
    currency: Currency
    original_amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    """Pre-discount price, if the listing shows one."""
    is_on_sale: bool = False

    @property
    def discount_ratio(self) -> float | None:
        """Fraction off original price, or ``None`` if no comparison available."""
        if self.original_amount is None or self.original_amount <= 0:
            return None
        if self.amount >= self.original_amount:
            return 0.0
        return float((self.original_amount - self.amount) / self.original_amount)


class MediaRef(_FrozenBase):
    """Reference to a media asset (image/video/audio) associated with the signal.

    We store the URL and a content hash so re-downloads are idempotent.
    The actual bytes live in MinIO (raw signal audit trail)."""

    url: HttpUrl
    modality: ContentModality
    """Expected to be one of ``IMAGE``, ``VIDEO``, ``AUDIO`` here."""

    width_px: NonNegativeInt | None = None
    height_px: NonNegativeInt | None = None
    duration_seconds: NonNegativeFloat | None = None
    byte_size: NonNegativeInt | None = None

    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32,64}$")] | None = None
    """Hex-encoded BLAKE2b of the downloaded bytes. Populated after the
    media-fetch stage; may be ``None`` at initial scrape time."""


class Location(_FrozenBase):
    """Geographic attribution. Used by geo-arbitrage + compliance tiers."""

    country_code: Annotated[str, StringConstraints(
        pattern=r"^[A-Z]{2}$", min_length=2, max_length=2,
    )] | None = None
    """ISO 3166-1 alpha-2 (e.g. ``US``, ``IN``, ``DE``)."""

    region: ShortText | None = None
    city: ShortText | None = None
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)


class ScrapeProvenance(_FrozenBase):
    """How and when this signal was captured. Entire block is required for audit."""

    method: ScrapeMethod
    scraped_at: AwareDatetime
    """UTC timestamp of the actual scrape event (not the upstream post time)."""

    scraper_version: ShortText
    """Semver or git-sha of the adapter that produced this signal."""

    proxy_id: ShortText | None = None
    """Pool ID of the proxy used; ``None`` if direct (e.g. official API)."""

    user_agent: ShortText | None = None
    ja3_fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")] | None = None
    """TLS client fingerprint hash. Masked per-request by ``curl-impersonate``."""

    tos_risk: ToSRisk
    """Risk classification for this scrape method × source combination."""

    rate_limit_hit: bool = False
    """Set to true if the scraper had to sleep for rate-limit cooldown."""

    captcha_encountered: bool = False
    """Set if a CAPTCHA was hit (and presumably bypassed via FlareSolverr)."""


class ConfidenceMetadata(_FrozenBase):
    """Quality / confidence signal for this record.

    These are scraper-reported quality hints. Downstream feature extractors
    may produce additional uncertainty estimates; those live on the feature
    record, not on the base signal."""

    completeness: float = Field(..., ge=0.0, le=1.0)
    """Fraction of optional fields that were successfully filled.
    Computed by the adapter; a quick sanity check on data quality."""

    freshness_seconds: NonNegativeFloat | None = None
    """How old the content is at scrape time. ``None`` when source does not
    expose an original post time."""

    source_confidence: float = Field(..., ge=0.0, le=1.0)
    """Adapter's self-reported confidence that the fields are correct (e.g.
    lower for parsed-from-HTML vs. official API)."""


class CrossModalCoherence(_FrozenBase):
    """Feature #8 (Omega v2) — text↔image alignment score.

    Populated later in the pipeline; initial scrape sets this ``None``."""

    text_image_similarity: float = Field(
        ..., ge=CROSS_MODAL_COHERENCE_MIN, le=CROSS_MODAL_COHERENCE_MAX,
    )
    """Cosine similarity between BGE-M3 text embedding and CLIP image embedding,
    projected into a shared 512-d space. Sharp drops (< -0.2) flag astroturfed
    or AI-generated content."""

    is_ai_generated_text: float | None = Field(default=None, ge=0.0, le=1.0)
    """Optional AI-text detector score. Heuristic; use cautiously."""


# =============================================================================
# The main ProductSignal
# =============================================================================


class ProductSignal(_FrozenBase):
    """Canonical representation of one signal from any upstream source.

    A "signal" is a single unit of actionable information — a TikTok post,
    a Reddit comment thread, an Etsy listing, a GDELT news event, etc.

    **Identity semantics**:

    - ``signal_id`` is OUR id (UUID v4, assigned at creation).
    - ``external_id`` is the platform's id (post id, listing id, event id).
    - ``content_hash`` is content-addressable; enables idempotent writes.

    Two signals with the same ``(platform, external_id)`` represent the same
    underlying item at different scrape times — use ``content_hash`` to detect
    whether the *content* changed between scrapes."""

    # --- Identity ----------------------------------------------------------
    signal_id: UUID = Field(default_factory=uuid4)
    """Our UUID. Immutable from creation — do not regenerate on re-scrape."""

    schema_version: str = Field(default=SCHEMA_VERSION, frozen=True)
    """Version of THIS schema the signal was produced against."""

    # --- Source attribution ------------------------------------------------
    platform: Platform
    tier: SourceTier
    """Redundant with ``platform_tier(platform)``, stored for query performance."""

    external_id: ShortText
    """Platform-native ID. Unique within a platform."""

    url: HttpUrl | None = None
    """Public URL to the original content, when one exists."""

    # --- Content -----------------------------------------------------------
    title: ShortText | None = None
    raw_text: LongText | None = None
    """Primary text content (caption, body, transcript, etc.) — PRE-scrub."""

    pii_scrubbed_text: LongText | None = None
    """``raw_text`` after the PII pipeline. This is what gets persisted + indexed."""

    language: Annotated[str, StringConstraints(
        pattern=r"^[a-z]{2}(-[A-Z]{2})?$",
    )] | None = None
    """BCP-47 tag like ``en``, ``en-US``, ``pt-BR``."""

    modality: ContentModality
    tags: frozenset[TagString] = Field(default_factory=frozenset)
    """Hashtags, keywords, category tags. ``frozenset`` so signals remain hashable."""

    intent: IntentType = IntentType.UNKNOWN
    """Inferred intent. Scrapers may set this heuristically; feature layer refines it."""

    # --- People, commerce, media ------------------------------------------
    author: Author | None = None
    """``None`` for anonymous / aggregate signals (e.g. Google Trends)."""

    engagement: EngagementMetrics | None = None
    price: Price | None = None
    """Required for T2 commerce signals; otherwise ``None``."""

    media: tuple[MediaRef, ...] = ()
    """Tuple (not list) — we enforce immutability for hashing."""

    location: Location | None = None

    # --- Temporal ---------------------------------------------------------
    posted_at: AwareDatetime | None = None
    """Upstream post/publication time. May be ``None`` for sources that don't expose it."""

    # --- Provenance / quality / features ----------------------------------
    provenance: ScrapeProvenance
    confidence: ConfidenceMetadata
    cross_modal: CrossModalCoherence | None = None
    """Populated AFTER embedding stage. ``None`` at initial scrape."""

    # --- Free-form bag (structured extras a specific source may emit) -----
    platform_specific: dict[str, Any] = Field(default_factory=dict)
    """Typed loosely on purpose — e.g. Reddit flair, TikTok music id.
    Downstream code MUST not rely on keys being present; treat as observations."""

    # --- Content hash (deterministic) -------------------------------------
    content_hash: Annotated[str, StringConstraints(
        pattern=r"^[0-9a-f]{32,64}$",
    )]
    """BLAKE2b digest (hex). Deterministic given the content fields below.
    Computed by ``compute_content_hash()``; validated in ``_check_hash_is_real``."""

    # ---------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------

    @field_validator("tier", mode="after")
    @classmethod
    def _tier_matches_platform(cls, v: SourceTier, info: Any) -> SourceTier:
        """Ensure ``tier`` is consistent with ``platform``. This catches
        adapters that hard-coded the wrong tier by mistake."""
        platform = info.data.get("platform")
        if platform is None:
            # Platform validation failed first; let that error surface.
            return v
        expected = platform_tier(platform)
        if v != expected:
            raise ValueError(
                f"tier mismatch: platform={platform!s} expects {expected!s}, got {v!s}",
            )
        return v

    @field_validator("posted_at")
    @classmethod
    def _enforce_utc(cls, v: datetime | None) -> datetime | None:
        """All timestamps must be timezone-aware UTC. Pydantic's
        ``AwareDatetime`` already requires tz-awareness; this validator
        additionally normalizes to UTC so we never accidentally persist
        an aware-but-non-UTC value."""
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _commerce_requires_price(self) -> Self:
        """T2 commerce signals SHOULD have a ``price``. We warn by raising —
        a T2 signal without a price is usually an adapter bug."""
        if self.tier == SourceTier.TIER_2_COMMERCE and self.price is None:
            raise ValueError(
                f"tier=T2_commerce requires a non-null price (platform={self.platform})",
            )
        return self

    @model_validator(mode="after")
    def _cross_modal_requires_media(self) -> Self:
        """A cross-modal coherence score only makes sense if there is both
        text content AND at least one media asset."""
        if self.cross_modal is not None:
            has_text = self.raw_text is not None or self.pii_scrubbed_text is not None
            has_media = any(
                m.modality in (ContentModality.IMAGE, ContentModality.VIDEO)
                for m in self.media
            )
            if not (has_text and has_media):
                raise ValueError(
                    "cross_modal is set but signal has no text+media pair to compare",
                )
        return self

    @model_validator(mode="after")
    def _check_hash_is_real(self) -> Self:
        """Re-derive the hash and compare. This is a cheap integrity check
        that catches signals whose content was mutated after hashing."""
        expected = self._derive_hash()
        if self.content_hash != expected:
            raise ValueError(
                f"content_hash mismatch: stored={self.content_hash[:12]}... "
                f"derived={expected[:12]}... — was the signal mutated after hashing?",
            )
        return self

    # ---------------------------------------------------------------------
    # Content hash — deterministic
    # ---------------------------------------------------------------------

    _HASH_INPUT_FIELDS: tuple[str, ...] = (
        "platform", "external_id", "url", "title", "raw_text", "posted_at",
    )
    """Fields that participate in the content hash. Adding a field here is a
    BREAKING change (all existing hashes invalidate) — bump SCHEMA_VERSION."""

    def _derive_hash(self) -> str:
        """Recompute the hash from the current field values.

        Uses BLAKE2b with a fixed digest size (see ``constants``). Inputs are
        concatenated with a null byte separator so that ``("a", "bc")`` and
        ``("ab", "c")`` hash differently — preventing collisions across
        variable-length fields.
        """
        h = _new_content_hasher()
        for name in self._HASH_INPUT_FIELDS:
            value = getattr(self, name)
            # Convert to a stable string form.
            if value is None:
                chunk = b"\x00NULL"
            elif isinstance(value, datetime):
                chunk = value.astimezone(UTC).isoformat().encode("utf-8")
            else:
                chunk = str(value).encode("utf-8")
            h.update(b"\x00")
            h.update(chunk)
        return h.hexdigest()

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    def with_hash(self) -> Self:
        """Return a copy of this signal with ``content_hash`` recomputed.

        Use this when constructing a signal — you cannot set the hash to a
        placeholder and then mutate other fields (the model is frozen).

        .. warning::

           ``model_copy(update=...)`` in pydantic v2 does NOT re-run validators
           by default. If you mutate content-hash-input fields via ``model_copy``
           and forget to call ``with_hash()``, downstream consumers may see a
           stale hash. Prefer rebuilding the signal from primitives whenever
           possible; use ``model_copy`` only for fields that do not participate
           in the hash.
        """
        # model_copy + update bypasses immutability for THIS specific op.
        derived = self._derive_hash()
        if self.content_hash == derived:
            return self
        return self.model_copy(update={"content_hash": derived})


def compute_content_hash(
    *,
    platform: Platform,
    external_id: str,
    url: str | None,
    title: str | None,
    raw_text: str | None,
    posted_at: datetime | None,
) -> str:
    """Compute the content hash without constructing a full ``ProductSignal``.

    Useful in scrapers that want to short-circuit (e.g. "have we seen this
    content before?" before doing expensive media downloads).
    """
    # Normalize URL the same way Pydantic's HttpUrl does (e.g. adds trailing
    # slash to bare origins) so compute_content_hash(url="https://x.com") ==
    # _derive_hash() which calls str(self.url) on an HttpUrl instance.
    if url is not None:
        with contextlib.suppress(Exception):
            url = str(HttpUrl(url))

    h = _new_content_hasher()
    for value in (platform.value, external_id, url, title, raw_text, posted_at):
        if value is None:
            chunk = b"\x00NULL"
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("posted_at must be timezone-aware for deterministic hashing")
            chunk = value.astimezone(UTC).isoformat().encode("utf-8")
        else:
            chunk = str(value).encode("utf-8")
        h.update(b"\x00")
        h.update(chunk)
    return h.hexdigest()


def _new_content_hasher() -> hashlib._Hash:
    """Return a fresh hasher matching the constants.

    BLAKE2b takes ``digest_size`` directly (variable-length output is one of
    its design features). SHA-256 has no equivalent; if you change
    ``SIGNAL_CONTENT_HASH_ALGO`` to ``sha256``, this branch raises so the
    mismatch is loud.
    """
    if SIGNAL_CONTENT_HASH_ALGO == "blake2b":
        return hashlib.blake2b(digest_size=SIGNAL_CONTENT_HASH_DIGEST_BYTES)
    if SIGNAL_CONTENT_HASH_ALGO == "blake2s":
        return hashlib.blake2s(digest_size=min(SIGNAL_CONTENT_HASH_DIGEST_BYTES, 32))
    # Fallback: any algo from hashlib.algorithms_guaranteed; digest size is fixed.
    raise ValueError(
        f"unsupported SIGNAL_CONTENT_HASH_ALGO={SIGNAL_CONTENT_HASH_ALGO!r}; "
        f"use blake2b or blake2s, or extend _new_content_hasher",
    )


__all__ = [
    "Author",
    "ConfidenceMetadata",
    "CrossModalCoherence",
    "EngagementMetrics",
    "Location",
    "MediaRef",
    "Price",
    "ProductSignal",
    "ScrapeProvenance",
    "compute_content_hash",
]
