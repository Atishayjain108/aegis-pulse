"""Global kill-switch for Phase 4 dispatch.

The kill-switch is a single Redis key (configurable via
``AEGIS_EXECUTE_KILLSWITCH_KEY``) whose presence with value
``"TRIPPED"`` halts every dispatch path in the system: the
``Pipeline.submit`` short-circuits before persisting, and the
``Drainer`` checks the switch on every tick before fanning out.

**Fail-closed by default.** If a Redis client is configured but the
backend is unreachable, ``is_tripped()`` returns ``True`` — the system
refuses to dispatch on uncertain state. In development without a Redis
client at all, the default flips to fail-open so a laptop can run with
no infrastructure.

Public surface:

* ``KillSwitch`` — the switch class, async API: ``is_tripped``,
  ``trip``, ``arm``, ``state``, ``raise_if_tripped``.
"""

from __future__ import annotations

from aegis.execute.killswitch.switch import KillSwitch

__all__ = ["KillSwitch"]
