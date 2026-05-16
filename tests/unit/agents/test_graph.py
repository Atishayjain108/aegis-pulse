"""
Unit tests for graph routing functions.

These tests exercise the conditional-edge predicates in isolation
(no langgraph compilation needed). They guarantee that each gate
makes the correct decision given a hand-crafted state — which is
where almost all the routing bugs would live.
"""

from __future__ import annotations

from aegis.agents.graph import (
    _post_compliance_route,
    _post_discovery_join,
    _post_hedge_route,
    _post_historian_route,
    _post_red_team_route,
    _post_sourcer_route,
)
from aegis.agents.schemas import AgentVerdict
from aegis.agents.state import initial_state


class TestPostDiscoveryJoin:
    def test_always_routes_to_historian(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        assert _post_discovery_join(state) == "historian"


class TestPostHistorianRoute:
    def test_strong_scout_routes_to_sourcer(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_score"] = 0.8  # type: ignore[typeddict-item]
        state["scout_verdict"] = AgentVerdict.PROCEED  # type: ignore[typeddict-item]
        assert _post_historian_route(state) == "sourcer"

    def test_weak_scout_skips_to_compliance(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_score"] = 0.20  # type: ignore[typeddict-item]
        state["scout_verdict"] = AgentVerdict.HOLD  # type: ignore[typeddict-item]
        assert _post_historian_route(state) == "compliance"

    def test_blocked_scout_skips_to_compliance(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_score"] = 0.9  # type: ignore[typeddict-item]
        state["scout_verdict"] = AgentVerdict.BLOCK  # type: ignore[typeddict-item]
        # Even with a high numeric score, BLOCK verdict short-circuits
        # to compliance so we record the trail without sourcing.
        assert _post_historian_route(state) == "compliance"

    def test_borderline_scout_at_threshold(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["scout_score"] = 0.55  # type: ignore[typeddict-item]
        state["scout_verdict"] = AgentVerdict.HOLD  # type: ignore[typeddict-item]
        # Exactly at the threshold → sourcer (>= comparison).
        assert _post_historian_route(state) == "sourcer"

    def test_missing_scout_score_defaults_low(self, trend_factory) -> None:
        # No scout fields in state → 0.0 default → compliance.
        state = initial_state(trend_factory())
        assert _post_historian_route(state) == "compliance"


class TestPostSourcerRoute:
    def test_supplier_present_routes_to_auditor(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["sourcer_supplier"] = {  # type: ignore[typeddict-item]
            "name": "AEGIS-Synth-AAA",
            "category": "novelty",
            "feasibility": "easy",
            "unit_cost": 2.0,
            "synthetic": True,
        }
        assert _post_sourcer_route(state) == "auditor"

    def test_no_supplier_skips_to_compliance(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["sourcer_supplier"] = None  # type: ignore[typeddict-item]
        assert _post_sourcer_route(state) == "compliance"

    def test_empty_dict_supplier_treated_as_no_supplier(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["sourcer_supplier"] = {}  # type: ignore[typeddict-item]
        # Empty dict → falsy → compliance.
        assert _post_sourcer_route(state) == "compliance"

    def test_non_dict_supplier_falls_back_to_compliance(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        # Defensive: somehow the field got set to a string.
        state["sourcer_supplier"] = "something invalid"  # type: ignore[typeddict-item]
        assert _post_sourcer_route(state) == "compliance"


class TestPostComplianceRoute:
    def test_passed_routes_to_red_team(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["compliance_passed"] = True  # type: ignore[typeddict-item]
        assert _post_compliance_route(state) == "red_team"

    def test_failed_skips_to_finalize(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["compliance_passed"] = False  # type: ignore[typeddict-item]
        assert _post_compliance_route(state) == "finalize"

    def test_missing_field_treated_as_passed(self, trend_factory) -> None:
        # If compliance never wrote, we don't have an *explicit* False,
        # so the route allows continuation. The supervisor's final
        # verdict still uses defaults.
        state = initial_state(trend_factory())
        assert _post_compliance_route(state) == "red_team"


class TestPostRedTeamRoute:
    def test_passed_routes_to_hedge(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["red_team_passed"] = True  # type: ignore[typeddict-item]
        assert _post_red_team_route(state) == "hedge"

    def test_failed_skips_to_finalize(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        state["red_team_passed"] = False  # type: ignore[typeddict-item]
        assert _post_red_team_route(state) == "finalize"

    def test_default_continues(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        assert _post_red_team_route(state) == "hedge"


class TestPostHedgeRoute:
    def test_always_finalizes(self, trend_factory) -> None:
        state = initial_state(trend_factory())
        assert _post_hedge_route(state) == "finalize"

        state["hedge_passed"] = False  # type: ignore[typeddict-item]
        # Even on failure, we still go to finalize — the supervisor
        # records the BLOCK and emits the GraphResult.
        assert _post_hedge_route(state) == "finalize"


class TestBuildGraphSmoke:
    """Smoke test: confirm the graph compiles when langgraph is available."""

    def test_compiles(self) -> None:
        # Skip gracefully if langgraph isn't installed.
        try:
            import langgraph  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            import pytest

            pytest.skip("langgraph not installed")

        from aegis.agents.graph import build_graph

        compiled = build_graph(use_llm=False)
        # Compiled graph exposes `ainvoke` regardless of langgraph
        # version; we just need it to be callable.
        assert hasattr(compiled, "ainvoke")
