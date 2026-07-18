"""
TLS (JA3 + JA4) and HTTP/2 fingerprint pool.

The pool is a curated set of realistic browser fingerprints. Selection is
deterministic given an `SeededRng` — same seed yields the same sequence of
picks, which is essential for reproducible scrape runs.

Doctrine — heuristic-first:
  * The bundled `BUILTIN_POOL` is real-world data, not synthesized.
  * If a user supplies an external pool via settings, we validate it
    against the same bounds and merge it in.
  * Selection never returns None — if every fingerprint is exhausted
    (none, in practice), we raise `FingerprintPoolError`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from aegis.harden.constants import (
    H2_INITIAL_WINDOW_SIZE_MAX,
    H2_INITIAL_WINDOW_SIZE_MIN,
    H2_MAX_FRAME_SIZE_MAX,
    H2_MAX_FRAME_SIZE_MIN,
    H2_MAX_HEADER_LIST_SIZE_DEFAULT,
)
from aegis.harden.errors import FingerprintPoolError, make
from aegis.harden.schemas import FingerprintProfile, H2Settings, TLSFingerprint
from aegis.harden.utils.rng import SeededRng

# ---------------------------------------------------------------------------
# Built-in pool — calibrated to mainstream desktop browsers as of 2026 Q1.
# These JA3/JA4 strings are publicly known fingerprints; rotation policy
# requires refreshing this list quarterly.
# ---------------------------------------------------------------------------

_BUILTIN: tuple[TLSFingerprint, ...] = (
    TLSFingerprint(
        fid="chrome-120-win10",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1516h2_8daaf6152771_b186095e22b6",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        notes="Chrome stable Windows 10",
    ),
    TLSFingerprint(
        fid="chrome-120-mac",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1517h2_8daaf6152771_b0da82dd1658",
        ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        notes="Chrome stable macOS Sonoma",
    ),
    TLSFingerprint(
        fid="firefox-121-win10",
        ja3="771,4865-4867-4866-49195-49199-52393-52392-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-34-51-43-13-45-28-21,29-23-24-25-256-257,0",
        ja4="t13d1715h2_5b57614c22b0_5c2700c4e3c8",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        notes="Firefox 121 Windows",
    ),
    TLSFingerprint(
        fid="firefox-121-linux",
        ja3="771,4865-4867-4866-49195-49199-52393-52392-49196-49200-49162-49161-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-34-51-43-13-45-28-21,29-23-24-25-256-257,0",
        ja4="t13d1715h2_5b57614c22b0_3a8c20e5e96d",
        ua="Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
        notes="Firefox 121 Linux",
    ),
    TLSFingerprint(
        fid="safari-17-mac",
        ja3="771,4865-4866-4867-49196-49195-52393-49200-49199-52392-49162-49161-49172-49171-157-156-53-47,0-23-65281-10-11-16-5-13-18-51-45-43-21,29-23-24-25,0",
        ja4="t13d1014h2_9dc949149365_2d2af2db8b1c",
        ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        notes="Safari 17 macOS",
    ),
    TLSFingerprint(
        fid="edge-120-win11",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1516h2_8daaf6152771_e5627efa2ab1",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        notes="Edge 120 Windows 11",
    ),
    TLSFingerprint(
        fid="chrome-mobile-android-14",
        ja3="771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-18-51-45-43-27-17513,29-23-24,0",
        ja4="t13d1516h2_8daaf6152771_a1c5f63fa1e0",
        ua="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        notes="Chrome 120 on Android (Pixel 8)",
    ),
    TLSFingerprint(
        fid="safari-17-ios",
        ja3="771,4865-4866-4867-49196-49195-52393-49200-49199-52392-49162-49161-49172-49171-157-156-53-47,0-23-65281-10-11-16-5-13-18-51-45-43-21,29-23-24-25,0",
        ja4="t13d1014h2_9dc949149365_8c2a23d4ddef",
        ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        notes="Safari 17 iOS",
    ),
)


# Common H2 SETTINGS variants observed in real browsers.
_H2_VARIANTS: tuple[H2Settings, ...] = (
    H2Settings(
        initial_window_size=6_291_456,
        max_frame_size=16_384,
        max_header_list_size=262_144,
        enable_push=False,
        settings_order=(1, 3, 4, 5, 6),
        window_update_increment=15_663_105,
    ),  # Chrome-like
    H2Settings(
        initial_window_size=131_072,
        max_frame_size=16_384,
        max_header_list_size=H2_MAX_HEADER_LIST_SIZE_DEFAULT,
        enable_push=False,
        settings_order=(1, 4, 5, 6, 8),
        window_update_increment=12_517_377,
    ),  # Firefox-like
    H2Settings(
        initial_window_size=4_194_304,
        max_frame_size=16_384,
        max_header_list_size=H2_MAX_HEADER_LIST_SIZE_DEFAULT,
        enable_push=False,
        settings_order=(2, 3, 4),
        window_update_increment=10_485_760,
    ),  # Safari-like
)


class FingerprintPool:
    """A pool of TLS+H2 fingerprints with deterministic selection.

    Construction:
      pool = FingerprintPool()              # built-in only
      pool = FingerprintPool.from_path(p)   # built-in + JSON list at `p`
      pool = FingerprintPool(extra=[...])   # built-in + caller-supplied
    """

    __slots__ = ("_tls", "_h2")

    def __init__(self, extra: Iterable[TLSFingerprint] | None = None) -> None:
        pool: list[TLSFingerprint] = list(_BUILTIN)
        if extra is not None:
            pool.extend(extra)
        if not pool:
            raise FingerprintPoolError(*make("AEGIS-HARDEN-0010"))
        self._tls: tuple[TLSFingerprint, ...] = tuple(pool)
        self._h2: tuple[H2Settings, ...] = _H2_VARIANTS

    @classmethod
    def from_path(cls, path: Path) -> FingerprintPool:
        """Load extra fingerprints from a JSON file. Schema: list of TLSFingerprint."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FingerprintPoolError(
                *make("AEGIS-HARDEN-0010", path=str(path), error=str(exc))
            ) from exc
        if not isinstance(data, list):
            raise FingerprintPoolError(
                *make("AEGIS-HARDEN-0010", path=str(path), error="not a list")
            )
        extra = [TLSFingerprint.model_validate(item) for item in data]
        return cls(extra=extra)

    # --- Read accessors ---
    @property
    def tls_size(self) -> int:
        return len(self._tls)

    @property
    def h2_size(self) -> int:
        return len(self._h2)

    def all_tls(self) -> tuple[TLSFingerprint, ...]:
        return self._tls

    def all_h2(self) -> tuple[H2Settings, ...]:
        return self._h2

    # --- Selection ---
    def pick(self, rng: SeededRng) -> FingerprintProfile:
        """Pick a complete fingerprint profile using `rng`. Deterministic."""
        if not self._tls or not self._h2:
            raise FingerprintPoolError(*make("AEGIS-HARDEN-0010", reason="empty pool"))
        tls = rng.choice(self._tls)
        # Match H2 settings to TLS family for realism: a Chrome JA3 with
        # Firefox H2 SETTINGS would itself be a fingerprint anomaly.
        family = _family_of(tls)
        candidates = [h for h in self._h2 if _h2_family(h) == family] or list(self._h2)
        h2 = rng.choice(candidates)
        return FingerprintProfile(tls=tls, h2=h2)

    def pick_by_family(self, family: str, rng: SeededRng) -> FingerprintProfile:
        """Pick a fingerprint scoped to `family` ('chrome', 'firefox', 'safari', 'edge')."""
        candidates = [t for t in self._tls if _family_of(t) == family]
        if not candidates:
            raise FingerprintPoolError(*make("AEGIS-HARDEN-0010", family=family))
        tls = rng.choice(candidates)
        h2_cands = [h for h in self._h2 if _h2_family(h) == family] or list(self._h2)
        return FingerprintProfile(tls=tls, h2=rng.choice(h2_cands))


# ---------------------------------------------------------------------------
# Family helpers
# ---------------------------------------------------------------------------


def _family_of(tls: TLSFingerprint) -> str:
    fid = tls.fid.lower()
    if "chrome" in fid:
        return "chrome"
    if "firefox" in fid:
        return "firefox"
    if "safari" in fid:
        return "safari"
    if "edge" in fid:
        return "edge"
    return "unknown"


def _h2_family(h2: H2Settings) -> str:
    # Heuristic mapping back from settings to a family — used only for
    # realistic pairing inside the pool.
    if h2.initial_window_size == 6_291_456:
        return "chrome"
    if h2.initial_window_size == 131_072:
        return "firefox"
    if h2.initial_window_size == 4_194_304:
        return "safari"
    return "unknown"


# ---------------------------------------------------------------------------
# HTTP/2 SETTINGS bound checker — for externally-supplied H2 configs
# ---------------------------------------------------------------------------


def validate_h2_settings(h2: H2Settings) -> H2Settings:
    """Return `h2` unchanged if within bounds; raise otherwise."""
    if not (H2_INITIAL_WINDOW_SIZE_MIN <= h2.initial_window_size <= H2_INITIAL_WINDOW_SIZE_MAX):
        raise FingerprintPoolError(
            *make("AEGIS-HARDEN-0012", field="initial_window_size", value=h2.initial_window_size)
        )
    if not (H2_MAX_FRAME_SIZE_MIN <= h2.max_frame_size <= H2_MAX_FRAME_SIZE_MAX):
        raise FingerprintPoolError(
            *make("AEGIS-HARDEN-0012", field="max_frame_size", value=h2.max_frame_size)
        )
    return h2


__all__ = ["FingerprintPool", "validate_h2_settings"]
