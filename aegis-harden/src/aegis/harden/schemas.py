"""
Phase 5 schemas — all frozen Pydantic v2 models.

These are the data contracts between Phase 5 and the rest of the system:
  * `FingerprintProfile` — bundle of TLS + HTTP/2 + UA selections.
  * `Playbook` — per-source scrape policy.
  * `HoneypotVerdict` — verdict on a candidate URL/DOM element.
  * `SmoothingResult` — output of the randomized-smoothing wrapper.
  * `PoisoningReport` — output of the poisoning detector.

Frozen models are immutable after construction — same doctrine as the rest
of the project (`TrendCandidate`, `Prediction`, `AlertEnvelope`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis.harden.constants import (
    HONEYPOT_BLOCK_THRESHOLD,
    HONEYPOT_WARN_THRESHOLD,
    PLAYBOOK_MAX_DELAY_MS,
    PLAYBOOK_MAX_RATE_PER_MIN,
    PLAYBOOK_MAX_RETRIES,
    PLAYBOOK_MIN_DELAY_MS,
    SMOOTHING_MAX_SAMPLES,
    SMOOTHING_MAX_SIGMA,
    SMOOTHING_MIN_SAMPLES,
    SMOOTHING_MIN_SIGMA,
)

# ---------------------------------------------------------------------------
# Common frozen-config base
# ---------------------------------------------------------------------------

_FROZEN = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class TLSFingerprint(BaseModel):
    """A single JA3 or JA4 fingerprint entry."""

    model_config = _FROZEN

    fid: str = Field(min_length=4, max_length=128, description="Stable id (e.g. 'chrome-120-mac').")
    ja3: str | None = Field(default=None, max_length=4096)
    ja4: str | None = Field(default=None, max_length=512)
    ua: str = Field(min_length=10, max_length=512)
    notes: str = Field(default="", max_length=256)

    @field_validator("ja3", "ja4")
    @classmethod
    def _no_empty(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v if v.strip() else None


class H2Settings(BaseModel):
    """HTTP/2 SETTINGS frame layout for fingerprint diversity."""

    model_config = _FROZEN

    initial_window_size: int = Field(ge=65_535, le=16_777_216)
    max_frame_size: int = Field(ge=16_384, le=16_777_215)
    max_header_list_size: int = Field(ge=4_096, le=1_048_576)
    enable_push: bool = Field(default=False)
    # Ordered SETTINGS identifier list — order is part of the fingerprint.
    settings_order: tuple[int, ...] = Field(default=(1, 3, 4, 5, 6))
    # WINDOW_UPDATE delta (RFC 7540 § 6.9). Real browsers emit ~15 MiB.
    window_update_increment: int = Field(ge=1, le=2_147_483_647, default=15_663_105)


class FingerprintProfile(BaseModel):
    """A composite fingerprint: TLS + HTTP/2 + UA."""

    model_config = _FROZEN

    tls: TLSFingerprint
    h2: H2Settings
    # Header order is part of the fingerprint; canonical browser order.
    header_order: tuple[str, ...] = Field(
        default=(
            ":method",
            ":authority",
            ":scheme",
            ":path",
            "accept",
            "accept-encoding",
            "accept-language",
            "cache-control",
            "user-agent",
            "referer",
        )
    )


# ---------------------------------------------------------------------------
# Playbook
# ---------------------------------------------------------------------------

PlaybookProfileLiteral = Literal["minimal", "standard", "stealth", "tor"]


class PlaybookMatch(BaseModel):
    """How a playbook matches an incoming URL or scrape target."""

    model_config = _FROZEN

    # Source identifier from Phase 1 (e.g. "reddit-rss", "amazon", "hacker-news").
    source: str | None = Field(default=None, min_length=1, max_length=64)
    # Or a domain glob — *.amazon.com, *.reddit.com, etc.
    domain: str | None = Field(default=None, min_length=3, max_length=256)
    # Optional URL substring guard.
    path_contains: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("source")
    @classmethod
    def _no_dot(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if "." in v:
            raise ValueError("source must be a slug, not a domain (use 'domain' field)")
        return v


class Playbook(BaseModel):
    """Per-source scrape policy. Loaded from YAML."""

    model_config = _FROZEN

    name: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1, le=1_000_000)
    match: PlaybookMatch
    delay_ms: int = Field(ge=PLAYBOOK_MIN_DELAY_MS, le=PLAYBOOK_MAX_DELAY_MS)
    jitter_ms: int = Field(ge=0, le=PLAYBOOK_MAX_DELAY_MS, default=0)
    rate_per_min: int = Field(ge=1, le=PLAYBOOK_MAX_RATE_PER_MIN)
    retries: int = Field(ge=0, le=PLAYBOOK_MAX_RETRIES)
    profile: PlaybookProfileLiteral = "standard"
    honor_robots: bool = True
    use_flaresolverr: bool = False
    rotate_every: int = Field(ge=1, le=10_000, default=10)
    notes: str = Field(default="", max_length=512)

    @field_validator("jitter_ms")
    @classmethod
    def _jitter_within(cls, v: int) -> int:
        return v  # bound checks already enforced; placeholder for cross-field rule

    def effective_delay_ms(self, n: int) -> int:
        """Compute the effective delay for the n-th request under this playbook.

        Pure function — no RNG, deterministic. Uses `n` to seed a Halton-like
        quasi-random offset so the sequence is reproducible from a request index.
        Callers can override with their own RNG if they need true randomness.
        """
        if self.jitter_ms == 0:
            return self.delay_ms
        # Van der Corput sequence (base 2) — deterministic, well-spread.
        offset = 0.0
        i = n + 1
        f = 0.5
        while i > 0:
            offset += f * (i % 2)
            i //= 2
            f *= 0.5
        return int(self.delay_ms + (offset - 0.5) * 2 * self.jitter_ms)


# ---------------------------------------------------------------------------
# Honeypot
# ---------------------------------------------------------------------------


class HoneypotVerdict(BaseModel):
    """Verdict on a candidate URL or DOM element."""

    model_config = _FROZEN

    url: str = Field(max_length=2048)
    score: float = Field(ge=0.0, le=1.0)
    blocked: bool
    reasons: tuple[str, ...] = Field(default=())
    detector_version: str = "1"

    @field_validator("blocked")
    @classmethod
    def _blocked_matches_score(cls, v: bool) -> bool:
        return v  # cross-field validated at construction time below

    @classmethod
    def make(cls, url: str, score: float, reasons: tuple[str, ...]) -> HoneypotVerdict:
        """Build a verdict with `blocked` derived from `score`."""
        score = max(0.0, min(1.0, float(score)))
        return cls(url=url, score=score, blocked=score >= HONEYPOT_BLOCK_THRESHOLD, reasons=reasons)

    @property
    def warn(self) -> bool:
        return (not self.blocked) and self.score >= HONEYPOT_WARN_THRESHOLD


# ---------------------------------------------------------------------------
# Randomized smoothing
# ---------------------------------------------------------------------------


class SmoothingResult(BaseModel):
    """Output of randomized smoothing applied to a single feature vector."""

    model_config = _FROZEN

    # The smoothed probability (mean of MC samples).
    smoothed_score: float = Field(ge=0.0, le=1.0)
    # Bound on the supremum-norm of the smoothed estimator from the empirical mean.
    certified_radius: float = Field(ge=0.0)
    # Inputs that produced this result.
    n_samples: int = Field(ge=SMOOTHING_MIN_SAMPLES, le=SMOOTHING_MAX_SAMPLES)
    sigma: float = Field(ge=SMOOTHING_MIN_SIGMA, le=SMOOTHING_MAX_SIGMA)
    # Whether the smoothed verdict matches the unsmoothed (raw) verdict.
    agrees_with_raw: bool
    raw_score: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Poisoning detection
# ---------------------------------------------------------------------------


class PoisoningSignal(BaseModel):
    """One detector's contribution to the overall poisoning verdict."""

    model_config = _FROZEN

    detector: str = Field(min_length=1, max_length=64)
    flagged: bool
    severity: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, float] = Field(default_factory=dict)


class PoisoningReport(BaseModel):
    """Aggregate verdict over a training batch."""

    model_config = _FROZEN

    batch_id: str = Field(min_length=1, max_length=128)
    n_samples: int = Field(ge=0)
    signals: tuple[PoisoningSignal, ...] = Field(default=())
    decision: Literal["accept", "warn", "reject"]
    generated_at: datetime = Field(default_factory=_utc_now)


# ---------------------------------------------------------------------------
# Phase 5 public verdict (the unified outbound type)
# ---------------------------------------------------------------------------


class HardenVerdict(BaseModel):
    """Unified outbound verdict consumed by Phase 4 alert composer.

    Maps 1:1 onto fields that the Phase 4 ComposerInput accepts.
    """

    model_config = _FROZEN

    trend_id: str | None = Field(default=None, max_length=128)
    source: Literal[
        "fingerprint",
        "playbook",
        "honeypot",
        "smoothing",
        "poisoning",
    ]
    verdict: Literal["proceed", "warn", "block"]
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: str = Field(min_length=1, max_length=64)
    detail: str = Field(default="", max_length=1024)
    generated_at: datetime = Field(default_factory=_utc_now)


__all__ = [
    "FingerprintProfile",
    "H2Settings",
    "HardenVerdict",
    "HoneypotVerdict",
    "Playbook",
    "PlaybookMatch",
    "PoisoningReport",
    "PoisoningSignal",
    "SmoothingResult",
    "TLSFingerprint",
]
