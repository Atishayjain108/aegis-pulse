"""Source-adapter scraping subsystem.

Phase 1 components:

- ``stealth`` — browser fingerprint masking primitives
- ``proxies`` — proxy pool with EMA health scoring
- ``cloudflare`` — FlareSolverr client (free CAPTCHA solver fallback)
- ``base`` — abstract ``SourceAdapter`` every per-source scraper inherits
- ``sources`` — concrete adapters (Reddit, YouTube, TikTok, …)
"""

from __future__ import annotations
