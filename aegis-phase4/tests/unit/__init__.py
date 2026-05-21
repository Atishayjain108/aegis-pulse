"""Unit-test tier for Phase 4.

Each test in this tree:

* runs in well under one second,
* imports nothing from outside ``aegis.execute`` (Phase 2 / Phase 3 are
  reached only via the structural ``Protocol`` bridges, never via real
  imports),
* uses no real database, Redis, or HTTP network — collaborators are
  stubbed inline or replaced via ``httpx.MockTransport``.
"""

from __future__ import annotations
