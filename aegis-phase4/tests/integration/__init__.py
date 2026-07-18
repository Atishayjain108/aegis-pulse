"""Integration-test tier for Phase 4.

These tests exercise full inter-module flows (Pipeline → Outbox →
Drainer, FastAPI request → SSE bus → notifier, etc.) using in-process
fakes — never real Postgres or Redis. The fakes live next to the tests
and are deliberately faithful to the production interfaces they mimic.
"""

from __future__ import annotations
