"""Advisory position sizing for Phase 4.

The sizing layer is purely advisory in v1 — Phase 4 emits an
``ExecutionIntent`` recording the recommended units and capital, but
never places an order. Phase 6 will consume these intents.

The default ``KellyAdvisor`` uses **fractional Kelly** (0.25× of the raw
Kelly fraction) with an additional hard cap at 10% of capital, per the
project's risk policy. A negative raw Kelly always produces zero units.

Public surface:

* ``KellyAdvisor`` — the advisor class. Constructor params let callers
  tighten the fraction or the per-trade cap further.
* ``SizingResult`` — frozen dataclass returned by ``advise()``.
"""

from __future__ import annotations

from aegis.execute.sizing.kelly import KellyAdvisor, SizingResult

__all__ = ["KellyAdvisor", "SizingResult"]
