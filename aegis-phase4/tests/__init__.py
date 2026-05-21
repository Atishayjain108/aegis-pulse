"""AEGIS Pulse — Phase 4 test root.

Tests are organised in two tiers:

* ``tests/unit`` — fast, pure-Python tests that import only the package.
  Every module here must run in well under a second and require no
  external services. Hypothetical Redis/Postgres clients are stubbed.

* ``tests/integration`` — tests that exercise multi-module flows
  (Pipeline → Outbox → Drainer, full FastAPI app, etc.) via the
  ``FakeRepository`` shipped under
  ``tests/integration/execute/conftest.py``. These tests still need no
  real infrastructure; the fake mirrors the asyncpg surface the
  production repository uses.

The top-level ``tests/conftest.py`` puts ``src/`` on ``sys.path`` so the
package imports without ``pip install``, and resets process-wide
singletons (settings, injected clock) between each test.
"""

from __future__ import annotations
