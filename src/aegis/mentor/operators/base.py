"""Operator contract — the one interface every company-function operator shares.

``Operator.run(profile, request, context) -> OperatorResult``. The result is a
grounded bundle: findings (each traceable to a source), actions, sources,
reasoning trace, and a calibrated confidence. Grounded-or-silent: an operator
returns an empty/low-confidence result rather than inventing data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from aegis.mentor.schemas import UserProfile
    from aegis.mentor.sector import SectorPack


@dataclass
class OperatorContext:
    """Shared dependencies handed to every operator.

    Plain mutable dataclass (not frozen) so the orchestrator can attach the
    resolved ``sector_pack`` once and reuse it across the fleet. Carries no
    secrets — just live handles + tuning.
    """

    pool: Any | None = None
    redis: Any | None = None
    sector_pack: SectorPack | None = None
    use_llm: bool = True
    depth: str = "standard"  # "surface" | "standard" | "deep"
    extras: dict[str, Any] = field(default_factory=dict)


class OperatorResult(BaseModel, frozen=True):
    """Grounded output of one operator run."""

    operator: str
    findings: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    reasoning: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_signal(self) -> bool:
        """True when the operator found something grounded to report."""
        return bool(self.findings or self.data)


@runtime_checkable
class Operator(Protocol):
    """Every operator owns one company function and speaks this contract."""

    name: str

    async def run(
        self,
        profile: UserProfile,
        request: str,
        context: OperatorContext,
    ) -> OperatorResult:
        """Produce a grounded result for ``request`` given the user ``profile``."""
        ...
