"""SupplierOp — find candidate suppliers and verify their reliability.

Grounded-or-silent sourcing. Candidate suppliers come only from real machinery
already in the repo:

* **Configured fulfillment channels** (Phase 6) — Printful POD, CJ Dropshipping,
  Shopify. A channel is only a candidate when its API credentials are actually
  configured in :class:`~aegis.execute.config.ExecuteSettings`; an unconfigured
  channel is never invented.
* **Printful catalog probe** — when a Printful key is present the operator asks
  the *real* catalog (``find_matching_product``) whether a variant exists for the
  user's product, recording that availability as a grounded sourcing fact.
* **Upstream candidates** — explicit rows handed in via
  ``context.extras["supplier_candidates"]`` (e.g. an IndiaMart B2B sourcing pass).

Reliability is verified by reusing the Phase D supplier-intel discipline
(:class:`aegis.execution_intel.supplier.SupplierIntel`): a supplier is **verified**
only when at least one *settled fulfillment* backs its trust score
(``SupplierTrustScore.is_verified``). Everything else is surfaced explicitly as
**unverified** — the operator never marks an unverified supplier as reliable.

Heuristic-first: ranking is deterministic (verified-first, trust desc, then price,
then lead-time). No LLM is required. Degrades to an empty result when nothing is
configured and no pool is available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from aegis.mentor.operators.base import OperatorContext, OperatorResult

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile

_log = structlog.get_logger("aegis.mentor.operators.supplier")


class SupplierOp:
    """Sourcing + reliability-verification operator."""

    name = "supplier"

    def __init__(
        self,
        *,
        intel: Any | None = None,
        printful: Any | None = None,
        settings: Any | None = None,
        candidate_source: Any | None = None,
    ) -> None:
        # All injectable so tests run with fakes and zero network.
        self._intel = intel              # SupplierIntel-like: trust_score(name)
        self._printful = printful        # PrintfulClient-like: find_matching_product
        self._settings = settings        # ExecuteSettings-like (fulfillment keys)
        self._candidate_source = candidate_source  # async fn(profile, request, ctx) -> rows

    # ------------------------------------------------------------------
    # Lazy dependency resolution
    # ------------------------------------------------------------------

    def _get_settings(self) -> Any | None:
        if self._settings is not None:
            return self._settings
        try:
            from aegis.execute.config import ExecuteSettings

            return ExecuteSettings()
        except Exception as exc:  # config absent in some envs — degrade quietly
            _log.debug("mentor.supplier.settings_unavailable", error=str(exc)[:200])
            return None

    def _get_printful(self, settings: Any | None) -> Any | None:
        if self._printful is not None:
            return self._printful
        key = getattr(settings, "printful_api_key", "") if settings else ""
        if not key:
            return None
        try:
            from aegis.fulfillment.printful import PrintfulClient

            return PrintfulClient(api_key=key)
        except Exception as exc:
            _log.debug("mentor.supplier.printful_unavailable", error=str(exc)[:200])
            return None

    def _get_intel(self, context: OperatorContext) -> Any | None:
        if self._intel is not None:
            return self._intel
        if context.pool is None:
            return None
        try:
            from aegis.execution_intel.supplier import SupplierIntel

            return SupplierIntel(context.pool)
        except Exception as exc:
            _log.debug("mentor.supplier.intel_unavailable", error=str(exc)[:200])
            return None

    # ------------------------------------------------------------------
    # Candidate sourcing (real channels only)
    # ------------------------------------------------------------------

    async def _gather_candidates(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
        settings: Any | None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        # 1. Upstream / injected candidate source (e.g. IndiaMart B2B sourcing).
        if self._candidate_source is not None:
            try:
                extra = await self._candidate_source(profile, request, context)
                rows.extend(extra or [])
            except Exception as exc:  # never sink the operator on a source failure
                _log.warning("mentor.supplier.candidate_source_failed", error=str(exc)[:200])

        # 2. Candidates passed through the orchestrator context.
        rows.extend(context.extras.get("supplier_candidates", []) or [])

        # 3. Configured fulfillment channels — only when credentials are present.
        if settings is not None:
            if getattr(settings, "printful_api_key", ""):
                rows.append({"name": "printful", "channel": "pod", "source": "fulfillment:printful"})
            if getattr(settings, "cjdropship_api_key", ""):
                rows.append(
                    {"name": "cjdropshipping", "channel": "dropship",
                     "source": "fulfillment:cjdropshipping"}
                )
            if getattr(settings, "shopify_access_token", "") and getattr(
                settings, "shopify_shop_domain", ""
            ):
                rows.append(
                    {"name": "shopify", "channel": "storefront", "source": "fulfillment:shopify"}
                )

        return self._dedup_by_name(rows)

    @staticmethod
    def _dedup_by_name(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            name = str(r.get("name", "")).strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            r = {**r, "name": name}
            out.append(r)
        return out

    async def _probe_printful(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
        rows: list[dict[str, Any]],
        settings: Any | None,
    ) -> None:
        """Ask the real Printful catalog whether a variant exists (availability)."""
        printful = self._get_printful(settings)
        if printful is None:
            return
        target = next((r for r in rows if r["name"] == "printful"), None)
        if target is None:
            return
        category = (profile.sector_raw or profile.sector or "").strip()
        keywords = [w for w in (request or "").split() if len(w) > 2][:5]
        try:
            product = await printful.find_matching_product(category, keywords)
        except Exception as exc:
            _log.debug("mentor.supplier.printful_probe_failed", error=str(exc)[:200])
            return
        if product is not None:
            target["catalog_available"] = True
            target["catalog_product"] = getattr(product, "name", None)
            target["variant_id"] = getattr(product, "variant_id", None)

    # ------------------------------------------------------------------
    # Reliability verification (Phase D discipline)
    # ------------------------------------------------------------------

    async def _verify(
        self, rows: list[dict[str, Any]], context: OperatorContext
    ) -> list[str]:
        """Attach measured trust to each row. Returns the sources consulted."""
        intel = self._get_intel(context)
        sources: list[str] = []
        if intel is None:
            for r in rows:
                r["verified"] = False
            return sources

        sources.append("execution_intel:supplier_reliability")
        for r in rows:
            try:
                score = await intel.trust_score(r["name"])
            except Exception as exc:
                _log.debug("mentor.supplier.trust_failed", name=r["name"], error=str(exc)[:200])
                r["verified"] = False
                continue
            # Verified ONLY when a settled fulfillment backs the score.
            if score is not None and getattr(score, "is_verified", False):
                r["verified"] = True
                r["trust"] = score.trust
                r["n_fulfillments"] = score.n_fulfillments
                r["delay_rate"] = score.delay_rate
                r["cancellation_rate"] = score.cancellation_rate
            else:
                r["verified"] = False
                r["verification_rate"] = getattr(score, "verification_rate", None) if score else None
        return sources

    # ------------------------------------------------------------------
    # Ranking (deterministic)
    # ------------------------------------------------------------------

    @staticmethod
    def _rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
        verified = bool(row.get("verified"))
        trust = row.get("trust")
        price = row.get("price")
        lead = row.get("lead_time_days")
        # verified-first; then highest trust; then cheapest; then fastest.
        return (
            0 if verified else 1,
            -(trust if isinstance(trust, int | float) else -1.0),
            price if isinstance(price, int | float) else float("inf"),
            lead if isinstance(lead, int | float) else float("inf"),
        )

    # ------------------------------------------------------------------
    # Operator entrypoint
    # ------------------------------------------------------------------

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        settings = self._get_settings()
        rows = await self._gather_candidates(profile, request, context, settings)

        if not rows:
            return OperatorResult(
                operator=self.name,
                reasoning=(
                    "No supplier candidates: no fulfillment channel is configured and "
                    "no candidates were supplied. Configure a supplier client or pass "
                    "candidates before sourcing."
                ),
                confidence=0.0,
            )

        await self._probe_printful(profile, request, context, rows, settings)
        sources = await self._verify(rows, context)
        rows.sort(key=self._rank_key)

        verified = [r for r in rows if r.get("verified")]
        unverified = [r for r in rows if not r.get("verified")]

        findings: list[str] = []
        for r in verified:
            trust = r.get("trust")
            n = r.get("n_fulfillments", 0)
            extra = self._row_detail(r)
            findings.append(
                f"Verified supplier: {r['name']} — trust "
                f"{round(trust, 3) if isinstance(trust, int | float) else 'n/a'} "
                f"over {n} settled fulfillment(s){extra}."
            )
        for r in unverified:
            extra = self._row_detail(r)
            findings.append(
                f"(unverified) {r['name']} ({r.get('channel', 'supplier')}) — "
                f"no settled fulfillment history yet; reliability NOT confirmed{extra}."
            )

        for r in rows:
            r.setdefault("verified", False)
        actions = self._build_actions(verified, unverified)
        confidence = self._confidence(verified, unverified)

        reasoning = (
            f"Sourced {len(rows)} candidate supplier(s); {len(verified)} verified by "
            f"settled fulfillment history, {len(unverified)} unverified. "
            "Unverified suppliers are flagged, never ranked as reliable."
        )

        return OperatorResult(
            operator=self.name,
            findings=findings,
            actions=actions,
            sources=sorted({*sources, *[str(r.get("source")) for r in rows if r.get("source")]}),
            reasoning=reasoning,
            confidence=confidence,
            data={
                "suppliers": rows,
                "verified_count": len(verified),
                "unverified_count": len(unverified),
            },
        )

    @staticmethod
    def _row_detail(row: dict[str, Any]) -> str:
        bits: list[str] = []
        price = row.get("price")
        if isinstance(price, int | float):
            bits.append(f"price {price} {row.get('currency', '')}".strip())
        lead = row.get("lead_time_days")
        if isinstance(lead, int | float):
            bits.append(f"lead {lead}d")
        if row.get("catalog_available"):
            bits.append("catalog match confirmed")
        return f" ({'; '.join(bits)})" if bits else ""

    @staticmethod
    def _build_actions(
        verified: list[dict[str, Any]], unverified: list[dict[str, Any]]
    ) -> list[str]:
        actions: list[str] = []
        if verified:
            actions.append(
                f"Start with the top verified supplier ({verified[0]['name']}) — its "
                "reliability is backed by real fulfillment history."
            )
        if unverified:
            actions.append(
                "Before committing capital, run a small test order to verify the "
                "unverified suppliers — their reliability is not yet confirmed."
            )
        if not verified:
            actions.append(
                "No supplier is verified yet: treat all candidates as unproven and "
                "verify with a test order before any capital-moving action (which "
                "stays behind the capital/compliance gate)."
            )
        return actions

    @staticmethod
    def _confidence(
        verified: list[dict[str, Any]], unverified: list[dict[str, Any]]
    ) -> float:
        if verified:
            trusts = [r["trust"] for r in verified if isinstance(r.get("trust"), int | float)]
            avg_trust = sum(trusts) / len(trusts) if trusts else 0.6
            return round(min(1.0, 0.4 + 0.5 * avg_trust + 0.05 * (len(verified) - 1)), 3)
        if unverified:
            # Real candidates exist but none is proven — deliberately low.
            return round(min(0.3, 0.1 + 0.05 * len(unverified)), 3)
        return 0.0
