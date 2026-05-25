"""Catalog schema migrations.

The catalog SQLite is created on first use by :class:`LakeCatalog._init_schema`
at ``schema_version = 1``. This module exists to evolve that schema safely in
future iterations.

Design
------
* Each migration is a forward-only SQL block keyed by an integer version.
* :func:`run_migrations` reads the current ``schema_version`` row and applies
  every higher-numbered migration in a single transaction per step.
* The migration runner is **idempotent** — re-running it after success is a
  no-op.
* Migrations are explicitly listed in ``_MIGRATIONS`` (not auto-discovered)
  so the upgrade path is reviewable in code review.

For Phase 10 v0.10.0 there are no migrations beyond the initial schema. The
machinery is here so that adding ``schema_version = 2`` later is trivial and
safe.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ._logging import get_logger
from .catalog.registry import LakeCatalog
from .errors import CatalogError

log = get_logger(__name__)


@dataclass(frozen=True)
class Migration:
    """A single forward-only migration step."""

    version: int
    description: str
    upgrade: Callable[[LakeCatalog], None]


# Registry of migrations. Append-only.
_MIGRATIONS: list[Migration] = []


def register_migration(m: Migration) -> None:
    """Append a migration (used by tests; production registers at import time)."""
    if any(existing.version == m.version for existing in _MIGRATIONS):
        raise CatalogError(f"duplicate migration version {m.version}")
    _MIGRATIONS.append(m)
    _MIGRATIONS.sort(key=lambda x: x.version)


def current_version(catalog: LakeCatalog) -> int:
    """Read the catalog's recorded schema version."""
    conn = catalog._get_conn()  # type: ignore[attr-defined]
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        return 0
    return int(row["version"])


def target_version() -> int:
    """The highest version we know how to upgrade to."""
    if not _MIGRATIONS:
        return 1  # baseline created by _init_schema
    return max(m.version for m in _MIGRATIONS)


def run_migrations(catalog: LakeCatalog) -> list[int]:
    """Apply every pending migration. Returns the list of versions applied."""
    applied: list[int] = []
    current = current_version(catalog)
    target = target_version()
    if current >= target:
        log.debug("migrations.up_to_date", current=current, target=target)
        return applied

    for m in _MIGRATIONS:
        if m.version <= current:
            continue
        log.info("migrations.applying", version=m.version, description=m.description)
        try:
            m.upgrade(catalog)
            with catalog._tx() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    "UPDATE schema_version SET version = ?",
                    (m.version,),
                )
            applied.append(m.version)
        except Exception as exc:
            raise CatalogError(
                f"migration {m.version} ({m.description}) failed: {exc}"
            ) from exc

    log.info(
        "migrations.complete",
        applied=applied,
        current=current,
        target=target,
    )
    return applied


__all__ = [
    "Migration",
    "current_version",
    "register_migration",
    "run_migrations",
    "target_version",
]
