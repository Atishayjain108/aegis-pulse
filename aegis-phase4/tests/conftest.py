"""Root pytest conftest.

Adds the src/ tree to sys.path so the package can be imported without
installation, and configures the default asyncio loop scope.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `aegis.execute` importable without `pip install`.
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Force a deterministic env for any settings that read env vars.
os.environ.setdefault("AEGIS_EXECUTE_MODE", "advisory")
os.environ.setdefault("AEGIS_EXECUTE_HMAC_KEY", "test-key-not-prod")

import pytest  # noqa: E402

from aegis.execute.config import reset_execute_settings  # noqa: E402
from aegis.execute.utils.time import reset_clock  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_globals() -> None:
    """Reset process-wide singletons between tests."""
    reset_execute_settings()
    reset_clock()
    yield
    reset_execute_settings()
    reset_clock()
