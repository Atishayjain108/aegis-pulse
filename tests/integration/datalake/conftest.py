"""Fixtures for the datalake integration suite.

These tests were written against the ``lake`` / ``sample_signal_rows``
fixtures that already exist in ``tests/unit/datalake/conftest.py`` — but
pytest does not share conftests across sibling trees, so this suite errored
with "fixture 'lake' not found" on its first-ever CI execution (2026-07-17;
the integration lane never got past the migration step before that day).

Re-exporting the canonical fixture objects keeps ONE definition of the
local-filesystem DataLake; nothing here may drift from the unit suite's.
"""

from tests.unit.datalake.conftest import (  # noqa: F401
    lake,
    sample_signal_rows,
    tmp_root,
    tmp_settings,
)
