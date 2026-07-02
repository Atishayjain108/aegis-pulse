"""Mentor operator fleet — one operator per company function.

Each operator shares one contract (:class:`~aegis.mentor.operators.base.Operator`):
``run(profile, request, context) -> OperatorResult``. The MentorAgent
orchestrates them. Operators are backed by AEGIS's real intelligence
(scrape/swarm, geo, compliance, capital, memory, trust) — they never fabricate.
"""

from __future__ import annotations

from aegis.mentor.operators.base import Operator, OperatorContext, OperatorResult
from aegis.mentor.operators.customer import CustomerOp
from aegis.mentor.operators.explore import ExploreOp
from aegis.mentor.operators.finance import FinanceOp
from aegis.mentor.operators.knowledge import KnowledgeOp, KnowledgeRetriever
from aegis.mentor.operators.ops import OpsOp
from aegis.mentor.operators.research import ResearchOp
from aegis.mentor.operators.supplier import SupplierOp

__all__ = [
    "CustomerOp",
    "ExploreOp",
    "FinanceOp",
    "KnowledgeOp",
    "KnowledgeRetriever",
    "Operator",
    "OperatorContext",
    "OperatorResult",
    "OpsOp",
    "ResearchOp",
    "SupplierOp",
]
