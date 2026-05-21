"""Adapters bridging Phase 2 + Phase 3 outputs to Phase 4 inputs.

The contract is:
  - Bridges accept upstream types via DUCK TYPING (never hard-imports
    Phase 2/3 modules at top-level beyond the type annotations) so that
    a degraded environment without LangGraph or torch installed still
    imports cleanly.
  - Bridges produce `ComposerInput` (a plain dataclass), which is the
    sole input to `aegis.execute.policy.composer.compose()`.

This indirection is what allows the policy layer to be unit-tested with
zero Phase 2/3 machinery present.
"""

from __future__ import annotations

from aegis.execute.bridge.types import ComposerInput

__all__ = ["ComposerInput"]
