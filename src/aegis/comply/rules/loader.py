"""Load and validate YAML rulesets into a :class:`RuleRegistry`.

The loader is strict about structure (raising ``RuleLoadError`` on malformed
files) but tolerant of an absent ``yaml`` dependency: if ``PyYAML`` is not
installed it falls back to a small built-in set of essential rules so the
engine still functions (graceful degradation).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from aegis.comply.errors import RuleLoadError
from aegis.comply.logging import get_logger
from aegis.comply.rules.base import Rule, RuleRegistry, RuleSet
from aegis.comply.schemas import Jurisdiction, RiskCategory, Severity

_log = get_logger("aegis.comply.rules.loader")

RULESET_DIR = Path(__file__).resolve().parent.parent / "rulesets"

try:
    import yaml

    _YAML_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without PyYAML
    _YAML_AVAILABLE = False


def _parse_rule(raw: dict[str, Any], *, default_jur: Jurisdiction, source: str) -> Rule:
    try:
        return Rule(
            id=str(raw["id"]),
            name=str(raw["name"]),
            severity=Severity(str(raw["severity"]).lower()),
            category=RiskCategory(str(raw["category"]).lower()),
            jurisdiction=Jurisdiction(str(raw.get("jurisdiction", default_jur.value)).upper()),
            match=raw["match"],
            citation=str(raw.get("citation", "")),
            remediation=str(raw.get("remediation", "")),
            description=str(raw.get("description", "")),
        )
    except (KeyError, ValueError) as exc:
        raise RuleLoadError(f"invalid rule in {source}: {exc}") from exc


def _parse_ruleset(doc: dict[str, Any], *, source: str) -> RuleSet:
    if not isinstance(doc, dict):
        raise RuleLoadError(f"{source}: top-level document must be a mapping")
    name = str(doc.get("ruleset", Path(source).stem))
    version = str(doc.get("version", "0"))
    try:
        jur = Jurisdiction(str(doc.get("jurisdiction", "GLOBAL")).upper())
    except ValueError as exc:
        raise RuleLoadError(f"{source}: bad jurisdiction: {exc}") from exc
    raw_rules = doc.get("rules", [])
    if not isinstance(raw_rules, list):
        raise RuleLoadError(f"{source}: 'rules' must be a list")
    rules = tuple(_parse_rule(r, default_jur=jur, source=source) for r in raw_rules)
    return RuleSet(name=name, version=version, jurisdiction=jur, rules=rules)


def load_registry(ruleset_dir: Path | None = None) -> RuleRegistry:
    """Load all ``*.yaml`` rulesets from ``ruleset_dir`` into a registry.

    Falls back to :func:`builtin_registry` when ``PyYAML`` is unavailable.
    """
    if not _YAML_AVAILABLE:
        _log.warning("rules.yaml_unavailable", action="using_builtin_registry")
        return builtin_registry()

    directory = ruleset_dir or RULESET_DIR
    if not directory.exists():
        _log.warning("rules.dir_missing", directory=str(directory))
        return builtin_registry()

    rulesets: list[RuleSet] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuleLoadError(f"failed to read {path.name}: {exc}") from exc
        rulesets.append(_parse_ruleset(doc, source=path.name))
    _log.info(
        "rules.loaded",
        rulesets=len(rulesets),
        rules=sum(len(rs.rules) for rs in rulesets),
    )
    return RuleRegistry(rulesets=tuple(rulesets))


@functools.lru_cache(maxsize=1)
def default_registry() -> RuleRegistry:
    """Process-cached default registry loaded from the packaged rulesets."""
    return load_registry()


def builtin_registry() -> RuleRegistry:
    """A minimal, dependency-free registry used when YAML is unavailable.

    Covers the highest-severity checks per jurisdiction so the engine never
    silently passes everything when ``PyYAML`` is missing.
    """
    rules = (
        Rule(
            id="FTC-HEALTH-001",
            name="Unsubstantiated health/cure claim",
            severity=Severity.BLOCK,
            category=RiskCategory.ADVERTISING,
            jurisdiction=Jurisdiction.US,
            match={
                "any_of": [
                    {
                        "field": "text",
                        "op": "contains_any",
                        "value": ["cures", "miracle cure", "guaranteed weight loss"],
                    }
                ]
            },
            citation="15 U.S.C. 45 (FTC Act)",
            remediation="Remove disease/cure claims or provide competent scientific evidence.",
        ),
        Rule(
            id="IN-DPDP-CONSENT-001",
            name="Personal data collection without privacy policy",
            severity=Severity.BLOCK,
            category=RiskCategory.PRIVACY,
            jurisdiction=Jurisdiction.IN,
            match={
                "all_of": [
                    {"field": "collects_personal_data", "op": "eq", "value": True},
                    {"field": "has_privacy_policy", "op": "eq", "value": False},
                ]
            },
            citation="DPDP Act 2023, s.6 (consent)",
            remediation="Publish a privacy notice and capture verifiable consent before collection.",
        ),
    )
    return RuleRegistry(
        rulesets=(
            RuleSet(name="builtin", version="0", jurisdiction=Jurisdiction.GLOBAL, rules=rules),
        )
    )
