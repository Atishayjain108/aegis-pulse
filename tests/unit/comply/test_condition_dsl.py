"""Tests for the rule condition DSL (combinators + leaf operators).

Leaf nodes are ``{"field": <ctx-key>, "op": <operator>, "value": <expected>}``.
Combinators are ``all_of`` / ``any_of`` / ``none_of`` (list) and ``not`` (node).
"""

from __future__ import annotations

import pytest

from aegis.comply.errors import ConditionEvalError
from aegis.comply.rules.base import build_context, evaluate_condition
from aegis.comply.schemas import ComplianceRequest


def _ctx(title="", **kw):
    return build_context(ComplianceRequest(trend_id="t", title=title, **kw))


def test_contains_any_on_text_field():
    ctx = _ctx(title="This product cures cancer fast")
    yes = {"field": "text", "op": "contains_any", "value": ["cures", "guaranteed"]}
    no = {"field": "text", "op": "contains_any", "value": ["unrelated"]}
    assert evaluate_condition(yes, ctx) is True
    assert evaluate_condition(no, ctx) is False


def test_all_of_requires_every_branch():
    ctx = _ctx(title="weight loss miracle")
    cond = {
        "all_of": [
            {"field": "text", "op": "contains_any", "value": ["weight loss"]},
            {"field": "text", "op": "contains_any", "value": ["miracle"]},
        ]
    }
    assert evaluate_condition(cond, ctx) is True
    cond_fail = {
        "all_of": [
            {"field": "text", "op": "contains_any", "value": ["weight loss"]},
            {"field": "text", "op": "contains_any", "value": ["nope"]},
        ]
    }
    assert evaluate_condition(cond_fail, ctx) is False


def test_any_of_and_none_of():
    ctx = _ctx(title="kids toy")
    any_cond = {
        "any_of": [
            {"field": "text", "op": "contains_any", "value": ["kids"]},
            {"field": "text", "op": "contains_any", "value": ["zzz"]},
        ]
    }
    assert evaluate_condition(any_cond, ctx) is True
    none_ok = {"none_of": [{"field": "text", "op": "contains_any", "value": ["adult"]}]}
    none_bad = {"none_of": [{"field": "text", "op": "contains_any", "value": ["kids"]}]}
    assert evaluate_condition(none_ok, ctx) is True
    assert evaluate_condition(none_bad, ctx) is False


def test_not_combinator():
    ctx = _ctx(title="safe item")
    cond = {"not": {"field": "text", "op": "contains_any", "value": ["danger"]}}
    assert evaluate_condition(cond, ctx) is True


def test_eq_ne_operators():
    ctx = _ctx(title="t", audience="children")
    assert evaluate_condition({"field": "audience", "op": "eq", "value": "children"}, ctx) is True
    assert evaluate_condition({"field": "audience", "op": "ne", "value": "adult"}, ctx) is True


def test_present_missing_operators():
    ctx = _ctx(title="t", collects_personal_data=True, has_privacy_policy=False)
    assert evaluate_condition({"field": "collects_personal_data", "op": "present"}, ctx) is True
    assert evaluate_condition({"field": "has_privacy_policy", "op": "missing"}, ctx) is True


def test_numeric_comparison_operators():
    ctx = _ctx(title="t", price=5.0)
    assert evaluate_condition({"field": "price", "op": "lt", "value": 10}, ctx) is True
    assert evaluate_condition({"field": "price", "op": "gte", "value": 5}, ctx) is True
    assert evaluate_condition({"field": "price", "op": "gt", "value": 100}, ctx) is False


def test_derived_audience_children_flag_from_category():
    ctx = _ctx(title="fun", category="kids-toys")
    assert ctx["audience_children"] is True


def test_regex_any_operator():
    ctx = _ctx(title="Earn $5000 per week guaranteed")
    assert evaluate_condition({"field": "text", "op": "regex_any", "value": [r"\$\d+"]}, ctx) is True
    assert evaluate_condition({"field": "text", "op": "regex_any", "value": [r"\bNOPE\b"]}, ctx) is False


def test_unknown_op_raises():
    with pytest.raises(ConditionEvalError):
        evaluate_condition({"field": "text", "op": "bogus", "value": 1}, _ctx(title="x"))
