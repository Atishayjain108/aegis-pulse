"""ProfileStore — persist + evolve UserProfiles (asyncpg, RLS).

Profiles are personal data, so every query sets ``app.current_tenant`` first
(RLS everywhere). One person → one evolving profile: ``upsert`` replaces the
row by ``profile_id``. Region defaults to India / INR.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from aegis.mentor.config import DEFAULT_TENANT_ID
from aegis.mentor.schemas import (
    AutonomyPreference,
    Channel,
    ExperienceLevel,
    Intent,
    RiskTolerance,
    UserProfile,
)

if TYPE_CHECKING:
    from asyncpg import Pool, Record

_log = structlog.get_logger("aegis.mentor.profiles")


class ProfileStore:
    """CRUD for ``user_profiles`` with row-level-security tenancy."""

    def __init__(self, db_pool: Pool, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._pool = db_pool
        self._tenant_id = tenant_id

    async def upsert(self, profile: UserProfile) -> bool:
        """Insert or update a profile by ``profile_id``. Returns success.

        Non-fatal: a persistence failure is logged and returns False so the
        Mentor can still advise in-memory without a DB.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                await conn.execute(
                    """
                    INSERT INTO user_profiles (
                        profile_id, tenant_id, raw_description, sector, sector_raw,
                        intent, autonomy_preference, capital_usd, risk_tolerance,
                        channels, skills, constraints, time_per_week_hrs,
                        experience_level, region, currency, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                        $14, $15, $16, $17, NOW()
                    )
                    ON CONFLICT (profile_id) DO UPDATE SET
                        raw_description     = EXCLUDED.raw_description,
                        sector              = EXCLUDED.sector,
                        sector_raw          = EXCLUDED.sector_raw,
                        intent              = EXCLUDED.intent,
                        autonomy_preference = EXCLUDED.autonomy_preference,
                        capital_usd         = EXCLUDED.capital_usd,
                        risk_tolerance      = EXCLUDED.risk_tolerance,
                        channels            = EXCLUDED.channels,
                        skills              = EXCLUDED.skills,
                        constraints         = EXCLUDED.constraints,
                        time_per_week_hrs   = EXCLUDED.time_per_week_hrs,
                        experience_level    = EXCLUDED.experience_level,
                        region              = EXCLUDED.region,
                        currency            = EXCLUDED.currency,
                        updated_at          = NOW()
                    """,
                    profile.profile_id,
                    profile.tenant_id,
                    profile.raw_description,
                    profile.sector,
                    profile.sector_raw,
                    profile.intent.value,
                    profile.autonomy_preference.value,
                    profile.capital_usd,
                    profile.risk_tolerance.value,
                    profile.channels.value,
                    json.dumps(profile.skills),
                    json.dumps(profile.constraints),
                    profile.time_per_week_hrs,
                    profile.experience_level.value,
                    profile.region,
                    profile.currency,
                    profile.created_at,
                )
            _log.info("mentor.profile.upserted", profile_id=profile.profile_id, sector=profile.sector)
            return True
        except Exception as exc:
            _log.warning("mentor.profile.upsert_failed", profile_id=profile.profile_id, error=str(exc))
            return False

    async def fetch(self, profile_id: str) -> UserProfile | None:
        """Fetch a profile by id, or None if absent / on error."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT set_config('app.current_tenant', $1, false)",
                    self._tenant_id,
                )
                row = await conn.fetchrow(
                    "SELECT * FROM user_profiles WHERE profile_id = $1",
                    profile_id,
                )
            if row is None:
                return None
            return self._row_to_profile(row)
        except Exception as exc:
            _log.warning("mentor.profile.fetch_failed", profile_id=profile_id, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_profile(row: Record) -> UserProfile:
        return UserProfile(
            profile_id=str(row["profile_id"]),
            tenant_id=str(row["tenant_id"]),
            raw_description=row["raw_description"] or "",
            sector=row["sector"],
            sector_raw=row["sector_raw"] or "",
            intent=Intent(row["intent"]),
            autonomy_preference=AutonomyPreference(row["autonomy_preference"]),
            capital_usd=float(row["capital_usd"]),
            risk_tolerance=RiskTolerance(row["risk_tolerance"]),
            channels=Channel(row["channels"]),
            skills=_load_json_list(row["skills"]),
            constraints=_load_json_list(row["constraints"]),
            time_per_week_hrs=float(row["time_per_week_hrs"]),
            experience_level=ExperienceLevel(row["experience_level"]),
            region=row["region"],
            currency=row["currency"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _load_json_list(value: object) -> list[str]:
    """asyncpg may return a JSON string or already-parsed list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []
