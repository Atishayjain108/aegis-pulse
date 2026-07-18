"""
Intellectual Property Rights (IPR) checker — Phase 8.

Two-pass strategy:
  Pass 1 (fast, <1 ms): keyword match against KNOWN_TRADEMARK_BRANDS dictionary.
  Pass 2 (live, ~5 s):  USPTO PatentsView REST API + EUIPO TMview REST API.

APIs used (both free, no key required for basic rate):
  - USPTO PatentsView  https://api.patentsview.org/patents/query
  - EUIPO TMview       https://www.tmdn.org/tmview/api/trademark/search
"""

from __future__ import annotations

import difflib
import time
from typing import Any

import httpx
import structlog

from aegis.compliance.config import ComplianceSettings
from aegis.compliance.constants import (
    EUIPO_TMVIEW_API,
    KNOWN_TRADEMARK_BRANDS,
    PATENTSVIEW_API,
)
from aegis.compliance.schemas import PatentMatch, TrademarkMatch

_log = structlog.get_logger("aegis.compliance.ipr")

# Minimum text similarity ratio to flag a fuzzy trademark hit
_FUZZY_TM_THRESHOLD = 0.82
# Maximum words to extract as keywords for patent search
_MAX_PATENT_KEYWORDS = 6
# Words ignored during keyword extraction
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "in", "of", "to", "for",
    "with", "is", "are", "was", "on", "at", "by", "from",
    "brand", "new", "high", "quality", "best", "premium",
})


class IPRChecker:
    """Trademark + patent infringement risk checker."""

    def __init__(self, settings: ComplianceSettings | None = None) -> None:
        self._cfg = settings or ComplianceSettings()
        self._tm_cache: dict[str, tuple[list[TrademarkMatch], float, float]] = {}
        self._patent_cache: dict[str, tuple[list[PatentMatch], float, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_trademark(
        self,
        product_title: str,
        product_description: str = "",
    ) -> tuple[list[TrademarkMatch], float]:
        """Return (matches, risk_score 0–1)."""
        cache_key = product_title.lower().strip()
        if cache_key in self._tm_cache:
            matches, risk, expire_at = self._tm_cache[cache_key]
            if time.monotonic() < expire_at:
                return matches, risk

        matches: list[TrademarkMatch] = []

        # Pass 1: fast local lookup
        matches.extend(self._fast_trademark_check(product_title))

        # Pass 2: live EUIPO TMview API (only if no clear local hit already)
        if not any(m.confidence_score > 0.9 for m in matches):
            keywords = self._extract_keywords(product_title)
            for kw in keywords[:3]:  # limit to 3 most meaningful words
                live = await self._search_euipo(kw)
                matches.extend(live)

        risk = self._compute_trademark_risk(matches, product_title)

        # Deduplicate by registration number
        seen: set[str] = set()
        unique: list[TrademarkMatch] = []
        for m in matches:
            if m.registration_number not in seen:
                seen.add(m.registration_number)
                unique.append(m)

        ttl = self._cfg.ipr_cache_ttl_hours * 3600
        self._tm_cache[cache_key] = (unique, risk, time.monotonic() + ttl)

        _log.info(
            "compliance.trademark_check",
            product=product_title[:60],
            matches=len(unique),
            risk_score=round(risk, 3),
        )
        return unique, risk

    async def check_patent(
        self,
        product_title: str,
        product_description: str = "",
    ) -> tuple[list[PatentMatch], float]:
        """Return (matches, risk_score 0–1).  High score = active granted patent."""
        cache_key = product_title.lower().strip()
        if cache_key in self._patent_cache:
            matches, risk, expire_at = self._patent_cache[cache_key]
            if time.monotonic() < expire_at:
                return matches, risk

        keywords = self._extract_keywords(product_title + " " + product_description)
        query_text = " ".join(keywords[:_MAX_PATENT_KEYWORDS])

        matches = await self._search_patentsview(query_text)
        risk = self._compute_patent_risk(matches)

        ttl = self._cfg.ipr_cache_ttl_hours * 3600
        self._patent_cache[cache_key] = (matches, risk, time.monotonic() + ttl)

        _log.info(
            "compliance.patent_check",
            product=product_title[:60],
            matches=len(matches),
            risk_score=round(risk, 3),
        )
        return matches, risk

    # ------------------------------------------------------------------
    # Pass 1: fast local dictionary check
    # ------------------------------------------------------------------

    def _fast_trademark_check(self, title: str) -> list[TrademarkMatch]:
        title_lower = title.lower()
        hits: list[TrademarkMatch] = []

        for brand_name, owner, category in KNOWN_TRADEMARK_BRANDS:
            # Exact substring match
            if brand_name in title_lower:
                hits.append(
                    TrademarkMatch(
                        registered_mark=brand_name.title(),
                        registration_number=f"LOCAL-{brand_name.upper().replace(' ', '-')}",
                        owner=owner,
                        status="registered",
                        goods_services=category,
                        jurisdiction="MULTI",
                        confidence_score=0.95,
                        source="local_brand_db",
                    )
                )
                continue

            # Fuzzy match: compare brand against each word in the title
            # (catches "guci" → "gucci", "adiddas" → "adidas", etc.)
            if len(brand_name) >= 4 and " " not in brand_name:
                best_ratio = 0.0
                for title_word in title_lower.split():
                    r = difflib.SequenceMatcher(None, brand_name, title_word).ratio()
                    best_ratio = max(r, best_ratio)
                if best_ratio >= _FUZZY_TM_THRESHOLD:
                    hits.append(
                        TrademarkMatch(
                            registered_mark=brand_name.title(),
                            registration_number=f"LOCAL-{brand_name.upper().replace(' ', '-')}-FUZZY",
                            owner=owner,
                            status="registered",
                            goods_services=category,
                            jurisdiction="MULTI",
                            confidence_score=round(best_ratio * 0.88, 3),
                            source="local_brand_db_fuzzy",
                        )
                    )

        return hits

    # ------------------------------------------------------------------
    # Pass 2: EUIPO TMview REST API
    # ------------------------------------------------------------------

    async def _search_euipo(self, keyword: str) -> list[TrademarkMatch]:
        """
        EUIPO TMview API — https://www.tmdn.org/tmview/api/trademark/search

        Free REST endpoint, no key required.
        Returns EU Trade Marks (EUTM) + member state registrations.
        """
        if len(keyword) < 3:
            return []
        try:
            async with httpx.AsyncClient(timeout=self._cfg.api_timeout_s) as client:
                resp = await client.get(
                    EUIPO_TMVIEW_API,
                    params={
                        "lang": "en",
                        "criteria": keyword,
                        "start": 0,
                        "rows": 5,
                        "source": "EUTM",
                        "status": "Registered",
                    },
                )
            if resp.status_code != 200:
                _log.debug("compliance.euipo_non_200", status=resp.status_code)
                return []

            data: dict[str, Any] = resp.json()
            return self._parse_euipo_response(data, keyword)

        except Exception as exc:
            _log.debug("compliance.euipo_search_error", error=str(exc)[:120])
            return []

    def _parse_euipo_response(
        self, data: dict[str, Any], query: str
    ) -> list[TrademarkMatch]:
        hits: list[TrademarkMatch] = []
        trademarks = data.get("trademarks", []) or data.get("results", [])
        for tm in trademarks:
            mark_name: str = (
                tm.get("markName") or tm.get("name") or ""
            ).strip()
            if not mark_name:
                continue

            # Similarity between queried keyword and registered mark name
            conf = difflib.SequenceMatcher(
                None, query.lower(), mark_name.lower()
            ).ratio()

            holder = tm.get("holder") or {}
            owner_name = (
                holder.get("name") if isinstance(holder, dict) else str(holder)
            ) or "Unknown"

            hits.append(
                TrademarkMatch(
                    registered_mark=mark_name,
                    registration_number=str(
                        tm.get("registrationNumber") or tm.get("applicationNumber") or "EU-UNKNOWN"
                    ),
                    owner=owner_name,
                    status=(tm.get("status") or "registered").lower(),
                    goods_services=str(tm.get("goodsServicesDescription") or "")[:200],
                    jurisdiction="EU",
                    confidence_score=round(min(conf * 1.1, 1.0), 3),
                    source="euipo_tmview",
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Pass 2: USPTO PatentsView REST API
    # ------------------------------------------------------------------

    async def _search_patentsview(self, query_text: str) -> list[PatentMatch]:
        """
        USPTO PatentsView API — https://api.patentsview.org/patents/query

        Free REST endpoint, no key required.
        Rate limit: 45 req/min without key; 3 000 req/day.
        """
        if not query_text.strip():
            return []
        try:
            async with httpx.AsyncClient(timeout=self._cfg.api_timeout_s) as client:
                resp = await client.post(
                    PATENTSVIEW_API,
                    json={
                        "q": {"_text_any": {"patent_title": query_text}},
                        "f": [
                            "patent_number",
                            "patent_title",
                            "patent_date",
                            "patent_abstract",
                        ],
                        "o": {"per_page": 5, "page": 1},
                    },
                )
            if resp.status_code != 200:
                _log.debug("compliance.patentsview_non_200", status=resp.status_code)
                return []

            data = resp.json()
            return self._parse_patentsview_response(data, query_text)

        except Exception as exc:
            _log.debug("compliance.patentsview_error", error=str(exc)[:120])
            return []

    def _parse_patentsview_response(
        self, data: dict[str, Any], query_text: str
    ) -> list[PatentMatch]:
        hits: list[PatentMatch] = []
        patents = data.get("patents") or []
        for p in patents:
            title: str = (p.get("patent_title") or "").strip()
            if not title:
                continue

            sim = difflib.SequenceMatcher(
                None, query_text.lower(), title.lower()
            ).ratio()

            # Only surface patents with non-trivial similarity
            if sim < 0.25:
                continue

            grant_date: str = p.get("patent_date") or ""
            # Active if granted in last 20 years (rough heuristic)
            status = "granted" if grant_date >= "2004" else "expired"

            hits.append(
                PatentMatch(
                    patent_number=str(p.get("patent_number") or ""),
                    patent_title=title,
                    abstract=str(p.get("patent_abstract") or "")[:300],
                    grant_date=grant_date,
                    status=status,
                    similarity_score=round(sim, 3),
                    source="patentsview",
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Risk computation
    # ------------------------------------------------------------------

    def _compute_trademark_risk(
        self, matches: list[TrademarkMatch], product_title: str
    ) -> float:
        if not matches:
            return 0.0
        active = [m for m in matches if m.status in ("registered", "pending")]
        if not active:
            return 0.05  # only expired marks
        max_conf = max(m.confidence_score for m in active)
        active_ratio = len(active) / max(1, len(matches))
        return min(1.0, max_conf * (0.7 + 0.3 * active_ratio))

    def _compute_patent_risk(self, matches: list[PatentMatch]) -> float:
        if not matches:
            return 0.0
        granted = [m for m in matches if m.status == "granted"]
        if not granted:
            return 0.05
        max_sim = max(m.similarity_score for m in granted)
        return min(1.0, max_sim * 0.80)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_keywords(self, text: str) -> list[str]:
        words = [
            w.strip(".,!?\"'()[]").lower()
            for w in text.split()
            if len(w) > 3 and w.lower() not in _STOPWORDS
        ]
        # Return unique, preserving first-occurrence order
        seen: set[str] = set()
        result: list[str] = []
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result
