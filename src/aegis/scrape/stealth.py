"""Browser fingerprint masking primitives.

Modern anti-bot systems (Cloudflare Bot Management v3, PerimeterX, Akamai Bot
Manager, DataDome) fingerprint headless browsers through dozens of signals
that diverge between automation and real users:

- ``navigator.webdriver`` flag
- Canvas-rendering pixel-precise differences
- WebGL renderer string (``UNMASKED_RENDERER_WEBGL``) revealing GPU + driver
- AudioContext output (always identical from headless Chrome)
- Font enumeration (headless containers usually have a tell-tale set)
- Hardware properties (``hardwareConcurrency``, ``deviceMemory``, ``maxTouchPoints``)
- Timezone vs. IP geo mismatch
- ``navigator.plugins`` array shape
- ``permissions.query()`` for ``notifications`` returning an unusual state
- Navigator language headers vs ``Accept-Language`` mismatch

This module produces a single JavaScript blob — the **stealth init script** —
that monkey-patches each surface to look like a real Chrome on a real laptop.
It composes with Playwright's ``page.add_init_script(...)`` and runs in every
new document/iframe before page scripts execute.

Design choices:

- **Pure data + pure JS** — no Playwright dependency at module level. Tests
  can introspect the generated JS without spinning up a browser.
- **Per-session randomisation** built into ``StealthProfile`` — every browser
  session gets a fresh-but-plausible identity. Re-rolling on every request
  would be detectable; we re-roll only between sessions.
- **patchright-friendly** — patchright already covers about 70% of the
  fingerprints we care about; this module fills in the remaining gaps and
  adds session-level diversity. We include a flag to skip overlapping work.

Note on ethics: this module exists to scrape **public** content where the
host's ToS does not legally forbid automated access. The ``ToSRisk`` tag on
each adapter (see ``schemas/enums.py``) gates which sources we apply this
to. We do NOT use stealth tooling against authenticated areas.

Author: AEGIS Pulse Team
Relationship: imported by ``scrape/base.py``, applied per-page in adapters.
"""

from __future__ import annotations

import json
import random
import secrets
from dataclasses import dataclass, field
from typing import Final

# =============================================================================
# Realistic value pools
# =============================================================================
# Each pool is a small, hand-curated set of values that ACTUALLY appear in
# unmodified Chrome on real hardware. Random selection from these pools yields
# a coherent fingerprint; values pulled from "uniform random over a wide
# range" are themselves a fingerprint giveaway.

# Chrome user agents — real builds + version sweep (q1/q2 2026 stable channel).
# Format: (user_agent, sec-ch-ua, platform, sec-ch-ua-platform-version)
_UA_POOL: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        "Windows", '"15.0.0"',
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "macOS", '"14.7.0"',
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "Linux", '""',
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "Windows", '"10.0.0"',
    ),
)

# Mobile Chrome (used when adapter wants mobile UA — TikTok, Instagram).
_UA_POOL_MOBILE: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36",
        '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        "Android", '"14.0.0"',
    ),
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        '""',  # Safari does not send sec-ch-ua
        "iOS", '"17.5.0"',
    ),
)

# Real WebGL renderer strings observed across consumer hardware.
_WEBGL_PROFILES: Final[tuple[tuple[str, str], ...]] = (
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6700 XT Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Apple)", "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)"),
    ("Mesa", "Mesa Intel(R) UHD Graphics (CML GT2)"),
)

# Common screen sizes — real laptops + desktops.
_SCREEN_PROFILES: Final[tuple[tuple[int, int, int], ...]] = (
    (1920, 1080, 24),     # Full HD desktop
    (1920, 1200, 24),     # 16:10 laptop
    (2560, 1440, 24),     # 1440p
    (1440, 900, 24),      # MBP 14" effective
    (1366, 768, 24),      # cheaper laptops
    (3840, 2160, 30),     # 4K — less common; included for diversity
)

# Hardware concurrency values (from real Chrome). Avoid values like 1, 2, 64
# that cluster in headless / dedicated-server fingerprints.
_HARDWARE_CONCURRENCY: Final[tuple[int, ...]] = (4, 6, 8, 8, 8, 12, 12, 16)

# deviceMemory in GB — Chrome only exposes the bucket {0.25, 0.5, 1, 2, 4, 8},
# clipped at 8 for privacy. Headless Chrome ALWAYS reports 8.
_DEVICE_MEMORY: Final[tuple[float, ...]] = (4, 4, 8, 8, 8)

# Common timezones — list pulled from real visitor analytics.
_TIMEZONES: Final[tuple[str, ...]] = (
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Toronto",
    "Europe/London", "Europe/Berlin", "Europe/Paris",
    "Europe/Madrid", "Europe/Amsterdam",
    "Asia/Kolkata", "Asia/Tokyo", "Asia/Singapore",
    "Australia/Sydney",
)

# Common Accept-Language headers, biased toward English-first since most of
# our targets are global English-language platforms. Diversity comes via
# regional dialect and weighted alternates.
_ACCEPT_LANGUAGES: Final[tuple[str, ...]] = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-CA,en;q=0.9,fr-CA;q=0.6",
    "en-AU,en;q=0.9",
    "en-IN,en;q=0.9,hi;q=0.6",
    "en-US,en;q=0.9,es;q=0.7",
    "en-DE,en;q=0.9,de;q=0.6",
)


# =============================================================================
# StealthProfile — the per-session identity
# =============================================================================


@dataclass(frozen=True, slots=True)
class StealthProfile:
    """One coherent browser identity for a single Playwright session.

    Constructed via ``StealthProfile.random()``; do NOT instantiate directly
    unless you've verified your values are internally consistent (e.g. mobile
    UA + desktop screen size = obvious bot).
    """

    user_agent: str
    sec_ch_ua: str
    platform: str
    sec_ch_ua_platform_version: str
    is_mobile: bool

    accept_language: str
    timezone: str
    locale: str
    """BCP-47 locale (e.g. ``en-US``). Drives navigator.language."""

    # Display / hardware
    screen_width: int
    screen_height: int
    color_depth: int
    hardware_concurrency: int
    device_memory: float
    max_touch_points: int

    # GPU
    webgl_vendor: str
    webgl_renderer: str

    # Per-session "noise" seeds — drive canvas/audio jitter so two sessions
    # leave different but-still-plausible fingerprints.
    canvas_noise_seed: int = field(default_factory=lambda: secrets.randbits(32))
    audio_noise_seed: int = field(default_factory=lambda: secrets.randbits(32))

    @classmethod
    def random(
        cls,
        *,
        mobile: bool = False,
        rng: random.Random | None = None,
    ) -> StealthProfile:
        """Generate a coherent random profile.

        Args:
            mobile: if True, sample from mobile UA pool with mobile-typical
                screen + touch settings.
            rng: optional RNG for reproducibility. Default uses a fresh
                ``random.Random()`` seeded by the OS entropy pool, NOT the
                global ``random.seed`` — so concurrent calls don't share state.

        Returns:
            A new ``StealthProfile`` instance.
        """
        r = rng or random.Random()

        if mobile:
            ua, sec_ua, plat, plat_ver = r.choice(_UA_POOL_MOBILE)
            screen_w, screen_h, color_depth = (390, 844, 24)  # iPhone 13/14 typical
            touch = 5
        else:
            ua, sec_ua, plat, plat_ver = r.choice(_UA_POOL)
            screen_w, screen_h, color_depth = r.choice(_SCREEN_PROFILES)
            touch = 0

        webgl_vendor, webgl_renderer = r.choice(_WEBGL_PROFILES)
        # Coherence rule: macOS + non-Apple GPU is suspicious. Re-pick if mismatched.
        if "Macintosh" in ua and "Apple" not in webgl_vendor:
            webgl_vendor, webgl_renderer = "Google Inc. (Apple)", "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)"

        accept_lang = r.choice(_ACCEPT_LANGUAGES)
        # Locale = first sub-tag of accept_lang (e.g. "en-US,en;q=0.9" → "en-US")
        locale = accept_lang.split(",", 1)[0].strip()

        return cls(
            user_agent=ua,
            sec_ch_ua=sec_ua,
            platform=plat,
            sec_ch_ua_platform_version=plat_ver,
            is_mobile=mobile,
            accept_language=accept_lang,
            timezone=r.choice(_TIMEZONES),
            locale=locale,
            screen_width=screen_w,
            screen_height=screen_h,
            color_depth=color_depth,
            hardware_concurrency=r.choice(_HARDWARE_CONCURRENCY),
            device_memory=r.choice(_DEVICE_MEMORY),
            max_touch_points=touch,
            webgl_vendor=webgl_vendor,
            webgl_renderer=webgl_renderer,
        )

    # ---- HTTP client conveniences ------------------------------------

    def http_headers(self) -> dict[str, str]:
        """Return a header dict suitable for httpx/curl-cffi/requests.

        Useful for pure-HTTP scrapers (no browser) that still want to look
        like a real Chrome request. Excludes Cookie / Authorization which
        the caller manages.
        """
        h = {
            "User-Agent": self.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": self.accept_language,
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        # sec-ch-ua only set for non-empty (Safari sends nothing).
        if self.sec_ch_ua and self.sec_ch_ua != '""':
            h["Sec-CH-UA"] = self.sec_ch_ua
            h["Sec-CH-UA-Platform"] = f'"{self.platform}"'
            h["Sec-CH-UA-Platform-Version"] = self.sec_ch_ua_platform_version
            h["Sec-CH-UA-Mobile"] = "?1" if self.is_mobile else "?0"
        return h


# =============================================================================
# Stealth init script generator
# =============================================================================


def build_stealth_script(profile: StealthProfile) -> str:
    """Return a JavaScript blob that hides automation signals.

    Inject via Playwright::

        await context.add_init_script(build_stealth_script(profile))

    This MUST be added to the BrowserContext (not Page) so it runs in every
    iframe and popup created from this context.

    The script is intentionally one big IIFE so we don't pollute window.
    """
    # JSON-encode the per-profile config so the JS is fully self-contained
    # and we don't need to interpolate strings into JavaScript (XSS-safe).
    cfg = {
        "userAgent": profile.user_agent,
        "platform": profile.platform,
        "language": profile.locale,
        "languages": _languages_array(profile.accept_language),
        "timezone": profile.timezone,
        "screenWidth": profile.screen_width,
        "screenHeight": profile.screen_height,
        "colorDepth": profile.color_depth,
        "hardwareConcurrency": profile.hardware_concurrency,
        "deviceMemory": profile.device_memory,
        "maxTouchPoints": profile.max_touch_points,
        "webglVendor": profile.webgl_vendor,
        "webglRenderer": profile.webgl_renderer,
        "canvasNoiseSeed": profile.canvas_noise_seed,
        "audioNoiseSeed": profile.audio_noise_seed,
    }
    cfg_json = json.dumps(cfg, separators=(",", ":"))

    # The script below patches one fingerprint surface per block. Each block
    # is independently revertible (the original property descriptor is left
    # alone for unrelated code paths). Comments precede each block.
    return _STEALTH_TEMPLATE.replace("__AEGIS_CFG__", cfg_json)


def _languages_array(accept_language: str) -> list[str]:
    """Convert ``Accept-Language`` header to a ``navigator.languages`` array.

    Example::

        "en-US,en;q=0.9" → ["en-US", "en"]
    """
    out: list[str] = []
    for part in accept_language.split(","):
        token = part.split(";", 1)[0].strip()
        if token and token not in out:
            out.append(token)
    return out


# Static script template. ``__AEGIS_CFG__`` is the only substitution point.
# Keep this readable — every modern anti-bot vendor reads it before publishing
# detection rules, so making it concise hurts detection-arms-race more than
# it helps the bot. We optimise for "matches real Chrome", not "minimal LoC".
#
# Coverage map:
#   1. webdriver flag
#   2. plugins array shape
#   3. mimeTypes array shape
#   4. permissions.query (notifications quirk)
#   5. languages
#   6. platform
#   7. hardwareConcurrency, deviceMemory, maxTouchPoints
#   8. screen dimensions
#   9. WebGL renderer/vendor (UNMASKED_*)
#  10. canvas getImageData jitter (deterministic from seed)
#  11. AudioContext getChannelData jitter
#  12. Intl.DateTimeFormat resolved timezone
#  13. chrome.runtime stub (real Chrome has window.chrome populated)
#  14. iframe contentWindow proxy fallthrough
_STEALTH_TEMPLATE: Final[str] = r"""
(() => {
  if (window.__aegisStealthApplied) return;
  window.__aegisStealthApplied = true;

  const cfg = __AEGIS_CFG__;

  // 1. webdriver — the canonical headless tell.
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined, configurable: true,
  });

  // 2 & 3. plugins / mimeTypes — empty arrays scream automation.
  // Chrome ships a small set even on fresh installs (PDF viewer at minimum).
  const fakePlugin = {
    name: 'Chrome PDF Viewer',
    description: 'Portable Document Format',
    filename: 'internal-pdf-viewer',
    length: 1,
  };
  Object.defineProperty(navigator, 'plugins', {
    get: () => Object.assign([fakePlugin], { length: 1, item: i => fakePlugin }),
    configurable: true,
  });
  Object.defineProperty(navigator, 'mimeTypes', {
    get: () => Object.assign(
      [{ type: 'application/pdf', suffixes: 'pdf', description: '' }],
      { length: 1 },
    ),
    configurable: true,
  });

  // 4. permissions.query for notifications — headless Chrome returns
  // 'denied' even when Notification.permission is 'default'. Real Chrome
  // returns whatever Notification.permission is.
  if (navigator.permissions && navigator.permissions.query) {
    const orig = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') {
        return Promise.resolve({
          state: Notification.permission || 'default',
          onchange: null,
        });
      }
      return orig(params);
    };
  }

  // 5 & 6. languages + platform.
  Object.defineProperty(navigator, 'languages', {
    get: () => cfg.languages, configurable: true,
  });
  Object.defineProperty(navigator, 'language', {
    get: () => cfg.language, configurable: true,
  });
  Object.defineProperty(navigator, 'platform', {
    get: () => cfg.platform === 'Windows' ? 'Win32'
            : cfg.platform === 'macOS'   ? 'MacIntel'
            : cfg.platform === 'Linux'   ? 'Linux x86_64'
            : cfg.platform === 'Android' ? 'Linux armv81'
            : cfg.platform === 'iOS'     ? 'iPhone'
            : 'Win32',
    configurable: true,
  });

  // 7. Hardware shape.
  Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => cfg.hardwareConcurrency, configurable: true,
  });
  Object.defineProperty(navigator, 'deviceMemory', {
    get: () => cfg.deviceMemory, configurable: true,
  });
  Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => cfg.maxTouchPoints, configurable: true,
  });

  // 8. Screen dims.
  Object.defineProperty(screen, 'width',       { get: () => cfg.screenWidth, configurable: true });
  Object.defineProperty(screen, 'height',      { get: () => cfg.screenHeight, configurable: true });
  Object.defineProperty(screen, 'availWidth',  { get: () => cfg.screenWidth, configurable: true });
  Object.defineProperty(screen, 'availHeight', { get: () => cfg.screenHeight - 40, configurable: true });
  Object.defineProperty(screen, 'colorDepth',  { get: () => cfg.colorDepth, configurable: true });
  Object.defineProperty(screen, 'pixelDepth',  { get: () => cfg.colorDepth, configurable: true });

  // 9. WebGL renderer/vendor — UNMASKED_VENDOR_WEBGL = 0x9245,
  // UNMASKED_RENDERER_WEBGL = 0x9246. These are extension-controlled.
  const patchGL = (ctx) => {
    const orig = ctx.getParameter;
    ctx.getParameter = function(p) {
      if (p === 0x9245) return cfg.webglVendor;
      if (p === 0x9246) return cfg.webglRenderer;
      return orig.call(this, p);
    };
  };
  const _getCtx = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, ...rest) {
    const ctx = _getCtx.call(this, type, ...rest);
    if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {
      try { patchGL(ctx); } catch (e) { /* read-only on some platforms */ }
    }
    return ctx;
  };

  // 10. Canvas pixel jitter — deterministic per-session noise so the same
  // session is internally consistent (otherwise repeated reads would
  // disagree, which is itself a tell). Seeded LCG.
  function makeRng(seed) {
    let s = seed >>> 0;
    return () => {
      s = Math.imul(s, 1664525) + 1013904223 | 0;
      return ((s >>> 8) & 0xff) / 255 - 0.5;  // [-0.5, 0.5]
    };
  }
  const canvasNoise = makeRng(cfg.canvasNoiseSeed);
  const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function(...args) {
    // Only perturb canvases that look like fingerprinting probes
    // (small, mostly-white). Don't break legitimate use.
    if (this.width >= 16 && this.width <= 250 && this.height >= 16 && this.height <= 60) {
      const ctx2d = _getCtx.call(this, '2d');
      if (ctx2d) {
        try {
          const img = ctx2d.getImageData(0, 0, this.width, this.height);
          for (let i = 0; i < img.data.length; i += 4 * 50) {
            img.data[i] = (img.data[i] + (canvasNoise() * 4 | 0)) & 0xff;
          }
          ctx2d.putImageData(img, 0, 0);
        } catch (e) { /* tainted canvas */ }
      }
    }
    return _toDataURL.apply(this, args);
  };

  // 11. AudioContext jitter — same idea, but cheaper because we only need
  // to perturb the float32 array returned by getChannelData.
  const audioNoise = makeRng(cfg.audioNoiseSeed);
  if (typeof AudioBuffer !== 'undefined') {
    const _getChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(channel) {
      const data = _getChannelData.call(this, channel);
      // Cheap LSB perturbation. Imperceptible audibly; defeats hash-based
      // fingerprints that compare bit-exact channel data.
      for (let i = 0; i < data.length; i += 1000) {
        data[i] = data[i] + audioNoise() * 1e-7;
      }
      return data;
    };
  }

  // 12. Timezone — Intl.DateTimeFormat() reads system tz. We replace the
  // resolvedOptions().timeZone return value so any code that asks for tz
  // gets our spoofed value.
  const _resolved = Intl.DateTimeFormat.prototype.resolvedOptions;
  Intl.DateTimeFormat.prototype.resolvedOptions = function() {
    const r = _resolved.call(this);
    r.timeZone = cfg.timezone;
    return r;
  };
  // Date.prototype.getTimezoneOffset is a separate vector — we don't lie
  // about offset because doing so without knowing actual local time would
  // be inconsistent. Real users WHOSE OS timezone matches their geolocation
  // will report the correct offset; if you need full geo masking, run
  // through a residential proxy in the same country as cfg.timezone.

  // 13. window.chrome stub — real Chrome has window.chrome.{runtime,csi,...}
  // populated; headless does not.
  if (!window.chrome) {
    window.chrome = {};
  }
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      OnInstalledReason: { INSTALL: 'install' },
      OnRestartRequiredReason: { APP_UPDATE: 'app_update' },
      PlatformOs: { ANDROID: 'android', LINUX: 'linux', MAC: 'mac', WIN: 'win' },
      RequestUpdateCheckStatus: { NO_UPDATE: 'no_update' },
    };
  }

  // 14. Iframe contentWindow access — defeat the trick of probing via
  // a same-origin iframe whose context bypassed our patches. Make any
  // newly-created iframe's contentWindow inherit our patched globals.
  // (This is best-effort; some sandboxed iframes are out of reach.)
  const _create = document.createElement.bind(document);
  document.createElement = function(tag, ...rest) {
    const el = _create(tag, ...rest);
    if (typeof tag === 'string' && tag.toLowerCase() === 'iframe') {
      el.addEventListener('load', () => {
        try {
          const w = el.contentWindow;
          if (w && !w.__aegisStealthApplied) {
            w.eval('(' + (window.__aegisStealthBootstrap || function(){}) + ')()');
          }
        } catch (e) { /* cross-origin */ }
      }, { once: true });
    }
    return el;
  };
})();
"""


# =============================================================================
# Apply helpers — Playwright integration is OPTIONAL at import time
# =============================================================================


async def apply_to_playwright_context(
    context: object,  # playwright.async_api.BrowserContext
    profile: StealthProfile,
) -> None:
    """Wire stealth onto an existing Playwright BrowserContext.

    Typed as ``object`` rather than the playwright class so this module
    imports without playwright installed (we don't want to force the heavy
    playwright dep onto pure-HTTP adapters).

    Equivalent to:

        await context.set_extra_http_headers({...})
        await context.add_init_script(build_stealth_script(profile))

    Plus a few setup calls that must happen at context-creation time:

        - timezone_id: must be set when the context is *created* (it reads
          IANA tz from the host's ICU). Cannot be patched live; document for
          callers in the docstring of their adapter.
        - locale: same constraint.
    """
    # We use getattr to avoid mypy/pyright complaining about an `object`
    # parameter — the real type comes from playwright at runtime.
    add_init_script = getattr(context, "add_init_script", None)
    set_extra_http_headers = getattr(context, "set_extra_http_headers", None)
    if not callable(add_init_script):
        raise TypeError(
            "context does not look like a Playwright BrowserContext "
            "(missing add_init_script)",
        )
    await add_init_script(script=build_stealth_script(profile))
    if callable(set_extra_http_headers):
        await set_extra_http_headers(profile.http_headers())


__all__ = [
    "StealthProfile",
    "apply_to_playwright_context",
    "build_stealth_script",
]
