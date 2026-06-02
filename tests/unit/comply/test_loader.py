"""Tests for the YAML rule loader and registry."""

from __future__ import annotations

from aegis.comply.rules.loader import builtin_registry, default_registry
from aegis.comply.schemas import ComplianceRequest, Jurisdiction


def test_default_registry_loads_rules_from_yaml():
    reg = default_registry()
    assert len(reg.all_rules) >= 10  # ftc + eu + india + fda rulesets


def test_default_registry_is_cached():
    assert default_registry() is default_registry()


def test_applicable_includes_global_and_target_jurisdiction():
    reg = default_registry()
    us_rules = reg.applicable({Jurisdiction.US})
    eu_rules = reg.applicable({Jurisdiction.EU})
    us_ids = {r.id for r in us_rules}
    eu_ids = {r.id for r in eu_rules}
    # EU-specific rule should not be in the US-only applicable set.
    assert any(r.jurisdiction is Jurisdiction.EU for r in eu_rules)
    assert not any(r.jurisdiction is Jurisdiction.EU for r in us_rules)
    # GPSR child-safety is an EU rule.
    assert "EU-GPSR-CHILD-SAFETY-001" in eu_ids
    assert "EU-GPSR-CHILD-SAFETY-001" not in us_ids


def test_india_dpdp_rule_fires_on_unconsented_data_collection():
    reg = default_registry()
    req = ComplianceRequest(
        trend_id="in-1",
        title="Newsletter signup widget",
        collects_personal_data=True,
        has_privacy_policy=False,
        target_jurisdictions=(Jurisdiction.IN,),
    )
    hits = reg.evaluate(req)
    assert any(h.rule_id.startswith("IN-DPDP") for h in hits)


def test_builtin_registry_fallback_has_essential_rules():
    reg = builtin_registry()
    ids = {r.id for r in reg.all_rules}
    assert "FTC-HEALTH-001" in ids
    assert "IN-DPDP-CONSENT-001" in ids


def test_rules_carry_metadata():
    reg = default_registry()
    for rule in reg.all_rules:
        assert rule.id
        assert rule.name
        assert rule.severity is not None
        assert rule.category is not None
