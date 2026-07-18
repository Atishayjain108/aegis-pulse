"""
Shared pytest fixtures for Phase 5.

Conventions:
  * Every test uses a `SeededRng(seed=...)` for reproducibility.
  * Network is never touched; all detectors are pure.

We also register two Hypothesis profiles so CI can switch behaviour via the
`HYPOTHESIS_PROFILE` env var:
  * `default` — 80 examples, 5s deadline. Used by local `make harden.test`.
  * `ci`      — 200 examples, no deadline. Used in GitHub Actions to catch
                more boundary cases.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

from aegis.harden.config import clear_settings
from aegis.harden.fingerprint import FingerprintPool
from aegis.harden.playbooks import builtin_registry
from aegis.harden.utils.clock import FixedClock
from aegis.harden.utils.rng import SeededRng

# Hypothesis profiles — registered once at conftest import time.
settings.register_profile(
    "default",
    max_examples=80,
    deadline=5_000,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    print_blob=True,  # easier to reproduce CI failures locally
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture(autouse=True)
def _no_settings_cache() -> None:
    """Clear the settings singleton between tests."""
    clear_settings()


@pytest.fixture
def rng() -> SeededRng:
    return SeededRng(seed=12345)


@pytest.fixture
def rng_alt() -> SeededRng:
    return SeededRng(seed=99999)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def fp_pool() -> FingerprintPool:
    return FingerprintPool()


@pytest.fixture
def reg():
    return builtin_registry()


@pytest.fixture
def playbooks_dir() -> Path:
    """Path to the bundled YAML playbooks directory."""
    return Path(__file__).resolve().parent.parent / "playbooks"
