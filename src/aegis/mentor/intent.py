"""IntentParser — free text → :class:`UserProfile`.

Heuristic-first: a deterministic regex/keyword extractor produces a full
profile with **zero** API keys. When the LLM gateway is available and
enrichment is enabled, it fills *only* fields the heuristics left unknown — it
can never overwrite a grounded extraction or fabricate a sector.

The one field AEGIS must not guess is **sector**. When sector or intent cannot
be determined, ``parse`` returns ``needs_clarification=True`` with a clarifying
question rather than proceeding on a guess.
"""

from __future__ import annotations

import contextlib
import json
import re

import structlog

from aegis.mentor.config import MentorSettings
from aegis.mentor.schemas import (
    SECTOR_UNDECIDED,
    AutonomyPreference,
    Channel,
    ExperienceLevel,
    Intent,
    IntentParseResult,
    RiskTolerance,
    UserProfile,
)
from aegis.mentor.sector import SectorRouter

_log = structlog.get_logger("aegis.mentor.intent")

# Approximate INR→USD for capital normalization (display only; not a live rate).
_INR_PER_USD = 83.0

_INTENT_KEYWORDS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.SCALE, ("scale", "grow", "expand", "already selling", "already running")),
    (Intent.VALIDATE, ("validate", "test the idea", "is this worth", "should i start")),
    (Intent.RESEARCH, ("research", "explore", "just looking", "curious", "studying", "learn about")),
    (Intent.LEARNING, ("learn", "teach me", "understand", "beginner", "how do i start")),
    (Intent.INCOME, ("make money", "earn", "income", "side income", "quit my job", "business to make")),
)

_AUTONOMY_KEYWORDS: tuple[tuple[AutonomyPreference, tuple[str, ...]], ...] = (
    (AutonomyPreference.GUIDE_ME, ("don't know anything", "dont know anything", "no idea",
                                   "do it for me", "guide me", "never done", "complete beginner",
                                   "hand-hold", "handhold")),
    (AutonomyPreference.ANSWER_ME, ("just give me", "brief me", "i know what i'm doing",
                                    "i know what im doing", "sharp analysis", "just the numbers",
                                    "experienced", "i run a")),
    (AutonomyPreference.COACH_ME, ("co-pilot", "work with me", "coach me", "help me decide")),
)


class IntentParser:
    """Parse a user's free-text description into a structured profile."""

    def __init__(
        self,
        *,
        settings: MentorSettings | None = None,
        router: SectorRouter | None = None,
    ) -> None:
        self._settings = settings or MentorSettings()
        self._router = router or SectorRouter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def parse(self, text: str) -> IntentParseResult:
        """Parse ``text`` into a profile, asking to clarify if sector/intent missing."""
        fields = self._heuristic_extract(text)
        method = "heuristic"

        if self._settings.llm_enrichment_enabled:
            llm_fields = await self._llm_enrich(text, fields)
            if llm_fields:
                # LLM only fills gaps; it never overwrites a grounded extraction.
                for key, value in llm_fields.items():
                    if fields.get(key) in (None, SECTOR_UNDECIDED, Intent.UNKNOWN, "", [], 0.0):
                        fields[key] = value
                method = "llm"

        profile = self._build_profile(text, fields)
        missing = self._missing_fields(profile)
        question = self._clarifying_question(missing) if missing else None

        _log.info(
            "mentor.intent.parsed",
            sector=profile.sector,
            intent=profile.intent.value,
            autonomy=profile.autonomy_preference.value,
            needs_clarification=bool(missing),
            method=method,
        )

        return IntentParseResult(
            profile=profile,
            needs_clarification=bool(missing),
            clarifying_question=question,
            missing_fields=tuple(missing),
            extraction_method=method,
        )

    # ------------------------------------------------------------------
    # Heuristic extraction (the deterministic, zero-key path)
    # ------------------------------------------------------------------

    def _heuristic_extract(self, text: str) -> dict[str, object]:
        lower = (text or "").lower()
        fields: dict[str, object] = {}

        fields["sector"] = self._router.normalize(text)
        fields["intent"] = self._first_match(lower, _INTENT_KEYWORDS, Intent.UNKNOWN)
        fields["autonomy"] = self._first_match(lower, _AUTONOMY_KEYWORDS, None)
        fields["capital_usd"] = self._extract_capital(lower)
        fields["constraints"] = self._extract_constraints(lower)
        fields["channels"] = self._extract_channel(lower, fields["constraints"])
        fields["experience"] = self._extract_experience(lower)
        fields["risk"] = self._extract_risk(lower)
        fields["time_per_week_hrs"] = self._extract_time(lower)
        return fields

    @staticmethod
    def _first_match(text: str, table: tuple, default):  # noqa: ANN001, ANN205
        for value, keywords in table:
            if any(kw in text for kw in keywords):
                return value
        return default

    def _extract_capital(self, text: str) -> float:
        # ₹0 / "no money" → 0.0 explicitly.
        if any(p in text for p in ("no money", "no capital", "zero capital", "₹0", "rs 0", "broke")):
            return 0.0
        # "5 lakh" / "2 crore"
        m = re.search(r"(\d+(?:\.\d+)?)\s*(lakh|lakhs|lac)", text)
        if m:
            return round(float(m.group(1)) * 100_000 / _INR_PER_USD, 2)
        m = re.search(r"(\d+(?:\.\d+)?)\s*(crore|crores|cr)\b", text)
        if m:
            return round(float(m.group(1)) * 10_000_000 / _INR_PER_USD, 2)
        # "$500" → USD directly.
        m = re.search(r"\$\s*(\d[\d,]*(?:\.\d+)?)", text)
        if m:
            return round(float(m.group(1).replace(",", "")), 2)
        # "₹50000" / "rs 50000" / "50000 rupees" → INR.
        m = re.search(r"(?:₹|rs\.?\s*)(\d[\d,]*)", text)
        if not m:
            m = re.search(r"(\d[\d,]{2,})\s*(?:rupees|inr)", text)
        if m:
            return round(float(m.group(1).replace(",", "")) / _INR_PER_USD, 2)
        # "50k" → INR by default (region IN).
        m = re.search(r"(\d+(?:\.\d+)?)\s*k\b", text)
        if m:
            return round(float(m.group(1)) * 1_000 / _INR_PER_USD, 2)
        return 0.0

    @staticmethod
    def _extract_constraints(text: str) -> list[str]:
        constraints: list[str] = []
        if any(p in text for p in ("never leave", "never leaves", "from home", "stay home",
                                   "work from home", "can't go out", "cant go out")):
            constraints.append("never_leaves_home")
        if any(p in text for p in ("no team", "alone", "solo", "by myself", "just me")):
            constraints.append("no_team")
        if any(p in text for p in ("part time", "part-time", "only weekends", "full time job",
                                   "have a job")):
            constraints.append("limited_time")
        return constraints

    @staticmethod
    def _extract_channel(text: str, constraints: list[str]) -> Channel:
        if "never_leaves_home" in constraints or any(
            p in text for p in ("online only", "online business", "from home", "ecommerce", "e-commerce")
        ):
            return Channel.ONLINE_ONLY
        if any(p in text for p in ("local", "shop", "kirana", "store", "in-person", "offline")):
            return Channel.LOCAL
        if "both" in text or "online and offline" in text:
            return Channel.BOTH
        return Channel.ONLINE_ONLY

    @staticmethod
    def _extract_experience(text: str) -> ExperienceLevel:
        if any(p in text for p in ("don't know anything", "dont know anything", "no idea",
                                   "complete beginner", "never done", "no experience")):
            return ExperienceLevel.NONE
        if any(p in text for p in ("experienced", "i run a", "already selling", "years of",
                                   "founder of", "running a business")):
            return ExperienceLevel.EXPERIENCED
        if any(p in text for p in ("some experience", "tried before", "dabbled", "a little")):
            return ExperienceLevel.SOME
        return ExperienceLevel.NONE

    @staticmethod
    def _extract_risk(text: str) -> RiskTolerance:
        if any(p in text for p in ("high risk", "aggressive", "go big", "all in")):
            return RiskTolerance.HIGH
        if any(p in text for p in ("low risk", "safe", "cautious", "careful", "can't afford to lose",
                                   "cant afford to lose")):
            return RiskTolerance.LOW
        return RiskTolerance.MEDIUM

    @staticmethod
    def _extract_time(text: str) -> float:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:a|per)\s*week", text)
        if m:
            return float(m.group(1))
        return 0.0

    # ------------------------------------------------------------------
    # Profile assembly
    # ------------------------------------------------------------------

    def _build_profile(self, text: str, fields: dict[str, object]) -> UserProfile:
        sector = fields.get("sector") or SECTOR_UNDECIDED
        intent = fields.get("intent") or Intent.UNKNOWN
        autonomy = fields.get("autonomy")
        experience = fields.get("experience") or ExperienceLevel.NONE

        # Default altitude: novices get it run *for* them; experienced get sharp.
        if autonomy is None:
            if experience == ExperienceLevel.EXPERIENCED:
                autonomy = AutonomyPreference.ANSWER_ME
            elif experience == ExperienceLevel.NONE:
                autonomy = AutonomyPreference.GUIDE_ME
            else:
                autonomy = AutonomyPreference.COACH_ME

        return UserProfile(
            tenant_id=self._settings.tenant_id,
            raw_description=text or "",
            sector=sector,  # type: ignore[arg-type]
            sector_raw=text or "",
            intent=intent,  # type: ignore[arg-type]
            autonomy_preference=autonomy,
            capital_usd=float(fields.get("capital_usd") or 0.0),
            risk_tolerance=fields.get("risk") or RiskTolerance.MEDIUM,  # type: ignore[arg-type]
            channels=fields.get("channels") or Channel.ONLINE_ONLY,  # type: ignore[arg-type]
            skills=list(fields.get("skills") or []),
            constraints=list(fields.get("constraints") or []),
            time_per_week_hrs=float(fields.get("time_per_week_hrs") or 0.0),
            experience_level=experience,  # type: ignore[arg-type]
            region=self._settings.default_region,
            currency=self._settings.default_currency,
        )

    @staticmethod
    def _missing_fields(profile: UserProfile) -> list[str]:
        missing: list[str] = []
        if profile.sector == SECTOR_UNDECIDED:
            missing.append("sector")
        if profile.intent == Intent.UNKNOWN:
            missing.append("intent")
        return missing

    @staticmethod
    def _clarifying_question(missing: list[str]) -> str:
        if "sector" in missing and "intent" in missing:
            return (
                "Before I can give you grounded advice — what field or kind of business "
                "are you interested in, and what do you want out of it (income, learning, "
                "or just exploring)?"
            )
        if "sector" in missing:
            return (
                "Which field or kind of business are you interested in? "
                "(e.g. selling products online, a local shop, freelancing, software)"
            )
        return "What do you want out of this — to earn income, to learn, or just to explore the space?"

    # ------------------------------------------------------------------
    # LLM enrichment (optional; never fabricates sector)
    # ------------------------------------------------------------------

    async def _llm_enrich(self, text: str, heuristic: dict[str, object]) -> dict[str, object]:
        """Ask the LLM to fill gaps the heuristics left. Best-effort, never fatal."""
        try:
            from aegis.llm.bridge.agents_bridge import complete_for_agent
        except Exception:
            return {}

        prompt = (
            "Extract a business-mentoring profile from the user's message as JSON with keys: "
            "sector_keywords (string), intent (one of income/learning/scale/research/validate/unknown). "
            "Only report what is explicitly stated; use 'unknown' if unclear. "
            f"Message: {text!r}"
        )
        try:
            raw = await complete_for_agent(
                "mentor_intent",
                [{"role": "user", "content": prompt}],
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_tokens,
            )
        except Exception:
            return {}

        out: dict[str, object] = {}
        try:
            data = json.loads(_extract_json(raw))
        except (json.JSONDecodeError, ValueError):
            return {}

        kw = data.get("sector_keywords")
        if isinstance(kw, str) and kw.strip():
            tag = self._router.normalize(kw)
            if tag != SECTOR_UNDECIDED:
                out["sector"] = tag
        intent_raw = data.get("intent")
        if isinstance(intent_raw, str):
            with contextlib.suppress(ValueError):
                out["intent"] = Intent(intent_raw.strip().lower())
        return out


def _extract_json(raw: str) -> str:
    """Pull the first JSON object out of an LLM response (handles code fences)."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no json object found")
    return raw[start : end + 1]
