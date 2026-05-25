"""Catalog migration runner tests."""

from __future__ import annotations

import pytest

from aegis.datalake.errors import CatalogError
from aegis.datalake.facade import DataLake
from aegis.datalake.migrations import (
    Migration,
    current_version,
    register_migration,
    run_migrations,
    target_version,
)


class TestMigrationRunner:
    def test_fresh_catalog_at_initial_version(self, lake: DataLake) -> None:
        # Fresh catalog should be at the schema version set by _init_schema.
        v = current_version(lake.catalog)
        assert v >= 1

    def test_no_pending_migrations_is_noop(self, lake: DataLake) -> None:
        applied = run_migrations(lake.catalog)
        assert applied == []

    def test_target_version_at_least_1(self) -> None:
        assert target_version() >= 1


class TestRegisterMigration:
    def test_register_duplicate_raises(self) -> None:
        # Use an absurdly high version to avoid clashing with anything real.
        m = Migration(
            version=99_999_001,
            description="test only",
            upgrade=lambda _cat: None,
        )
        register_migration(m)
        with pytest.raises(CatalogError):
            register_migration(m)

    def test_migrations_stay_sorted(self) -> None:
        from aegis.datalake.migrations import _MIGRATIONS

        versions = [m.version for m in _MIGRATIONS]
        assert versions == sorted(versions)
