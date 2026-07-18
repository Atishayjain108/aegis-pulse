"""Data-driven rule engine.

Rules are declared in YAML (see ``aegis/comply/rulesets/*.yaml``) — never as
hardcoded if/else — and evaluated against a flat context built from a
``ComplianceRequest``. A rule fires when its ``match`` condition tree evaluates
true; firing produces a :class:`~aegis.comply.schemas.RuleHit`.

Condition DSL
-------------
A node is either a *combinator* or a *leaf*.

Combinators (exactly one key)::

    {"all_of": [<node>, ...]}    # logical AND
    {"any_of": [<node>, ...]}    # logical OR
    {"none_of": [<node>, ...]}   # NOR
    {"not": <node>}              # NOT

Leaf::

    {"field": "<name>", "op": "<op>", "value": <any>}

Supported ops: ``eq ne gt lt gte lte in not_in contains_any contains_all
regex_any present missing``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aegis.comply.errors import ConditionEvalError
from aegis.comply.schemas import (
    ComplianceRequest,
    Jurisdiction,
    RiskCategory,
    RuleHit,
    Severity,
)

_COMBINATORS = {"all_of", "any_of", "none_of", "not"}


def build_context(request: ComplianceRequest) -> dict[str, Any]:
    """Flatten a request into a context dict the condition DSL can evaluate."""
    cat = request.category.lower()
    audience = request.audience.lower()
    children = (
        audience in {"children", "kids", "minors"}
        or "kid" in cat
        or "toy" in cat
        or "child" in cat
    )
    return {
        "title": request.title.lower(),
        "description": request.description.lower(),
        "category": cat,
        "audience": audience,
        "audience_children": children,
        "text": request.text,
        "claims_text": " ".join(c.lower() for c in request.claims),
        "price": request.price,
        "currency": request.currency.upper(),
        "endorsement_present": request.endorsement_present,
        "disclosure_present": request.disclosure_present,
        "collects_personal_data": request.collects_personal_data,
        "has_privacy_policy": request.has_privacy_policy,
        "has_age_gate": request.has_age_gate,
        "jurisdictions": {j.value for j in request.target_jurisdictions},
    }


def _coerce_str(value: Any) -> str:
    return "" if value is None else str(value).lower()


def _eval_leaf(node: Mapping[str, Any], ctx: Mapping[str, Any]) -> bool:
    try:
        field_name = node["field"]
        op = node["op"]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ConditionEvalError(f"leaf missing key: {exc}") from exc
    value = ctx.get(field_name)
    expected = node.get("value")

    if op == "present":
        return bool(value)
    if op == "missing":
        return not bool(value)
    if op == "eq":
        return value == expected
    if op == "ne":
        return value != expected
    if op in {"gt", "lt", "gte", "lte"}:
        if value is None or expected is None:
            return False
        try:
            a, b = float(value), float(expected)
        except (TypeError, ValueError):
            return False
        return {"gt": a > b, "lt": a < b, "gte": a >= b, "lte": a <= b}[op]
    if op == "in":
        return value in (expected or [])
    if op == "not_in":
        return value not in (expected or [])
    if op == "contains_any":
        hay = _coerce_str(value)
        return any(_coerce_str(n) in hay for n in (expected or []))
    if op == "contains_all":
        hay = _coerce_str(value)
        return all(_coerce_str(n) in hay for n in (expected or []))
    if op == "regex_any":
        hay = _coerce_str(value)
        return any(re.search(str(p), hay, re.IGNORECASE) for p in (expected or []))
    raise ConditionEvalError(f"unknown op: {op!r}")


def evaluate_condition(node: Mapping[str, Any], ctx: Mapping[str, Any]) -> bool:
    """Recursively evaluate a condition node against a context."""
    if not isinstance(node, Mapping):
        raise ConditionEvalError(f"condition node must be a mapping, got {type(node)}")
    combinator_keys = _COMBINATORS & set(node.keys())
    if combinator_keys:
        if len(combinator_keys) != 1:
            raise ConditionEvalError(f"multiple combinators in one node: {combinator_keys}")
        key = combinator_keys.pop()
        if key == "not":
            return not evaluate_condition(node["not"], ctx)
        children = node[key]
        if not isinstance(children, list):
            raise ConditionEvalError(f"{key} expects a list")
        results = (evaluate_condition(c, ctx) for c in children)
        if key == "all_of":
            return all(results)
        if key == "any_of":
            return any(results)
        return not any(results)  # none_of
    return _eval_leaf(node, ctx)


@dataclass(frozen=True)
class Rule:
    """A single declarative compliance rule."""

    id: str
    name: str
    severity: Severity
    category: RiskCategory
    jurisdiction: Jurisdiction
    match: Mapping[str, Any]
    citation: str = ""
    remediation: str = ""
    description: str = ""

    def evaluate(self, ctx: Mapping[str, Any]) -> RuleHit | None:
        """Return a :class:`RuleHit` if this rule's condition matches, else ``None``."""
        if evaluate_condition(self.match, ctx):
            return RuleHit(
                rule_id=self.id,
                name=self.name,
                severity=self.severity,
                category=self.category,
                jurisdiction=self.jurisdiction,
                citation=self.citation,
                remediation=self.remediation,
                detail=self.description,
            )
        return None


@dataclass(frozen=True)
class RuleSet:
    """A versioned collection of rules for one jurisdiction/ruleset name."""

    name: str
    version: str
    jurisdiction: Jurisdiction
    rules: tuple[Rule, ...] = field(default_factory=tuple)


@dataclass
class RuleRegistry:
    """In-memory registry of all loaded rulesets."""

    rulesets: tuple[RuleSet, ...] = field(default_factory=tuple)

    @property
    def all_rules(self) -> tuple[Rule, ...]:
        return tuple(r for rs in self.rulesets for r in rs.rules)

    def applicable(self, targets: set[Jurisdiction]) -> tuple[Rule, ...]:
        """Rules whose jurisdiction is in ``targets`` or is ``GLOBAL``."""
        return tuple(
            r
            for r in self.all_rules
            if r.jurisdiction == Jurisdiction.GLOBAL or r.jurisdiction in targets
        )

    def evaluate(self, request: ComplianceRequest) -> list[RuleHit]:
        """Evaluate every applicable rule against ``request``."""
        ctx = build_context(request)
        targets = set(request.target_jurisdictions)
        hits: list[RuleHit] = []
        for rule in self.applicable(targets):
            hit = rule.evaluate(ctx)
            if hit is not None:
                hits.append(hit)
        return hits
