"""tests/unit/llm/test_bridge.py — Bridge layer tests"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_response(content: str = "Generated alert text"):
    from aegis.llm.gateway.response import LLMResponse, TokenUsage
    return LLMResponse(content=content, provider="ollama", model="test",
                       usage=TokenUsage(10, 20, 30), latency_ms=50.0)


class TestPhase3Bridge:
    @pytest.mark.asyncio()
    async def test_generate_rationale_success(self):
        from aegis.llm.bridge import phase3_bridge

        mock_gw = MagicMock()
        mock_gw.complete = AsyncMock(return_value=_make_response("Strong upward momentum."))

        with patch("aegis.llm.bridge.agents_bridge._gateway", mock_gw):
            result = await phase3_bridge.generate_prediction_rationale(
                trend_title="AI chips",
                verdict="ENTER",
                p_breakout=0.85,
                p_decline=0.10,
                confidence=0.80,
                horizon_h=24,
                velocity_1h=42.0,
                sentiment=0.7,
                novelty=0.6,
            )
        assert len(result) > 0

    @pytest.mark.asyncio()
    async def test_generate_rationale_fallback_on_error(self):
        from aegis.llm.bridge import phase3_bridge

        with patch("aegis.llm.bridge.agents_bridge._gateway", None):
            # Should use fallback when gateway unavailable
            with patch("aegis.llm.bridge.agents_bridge.get_gateway", side_effect=Exception("no gw")):
                result = await phase3_bridge.generate_prediction_rationale(
                    trend_title="Test trend", verdict="HOLD",
                    p_breakout=0.4, p_decline=0.3, confidence=0.6,
                    horizon_h=6, velocity_1h=10.0, sentiment=0.2, novelty=0.3,
                )
        # Fallback should still return a non-empty string
        assert "Test trend" in result

    @pytest.mark.asyncio()
    async def test_generate_causal_explanation_fallback(self):
        from aegis.llm.bridge import phase3_bridge
        with patch("aegis.llm.bridge.agents_bridge.get_gateway", side_effect=Exception("unavail")):
            result = await phase3_bridge.generate_causal_explanation(
                "AI chips", "+300% velocity spike",
                [{"factor": "viral_video", "contribution": 0.6}]
            )
        assert "AI chips" in result


class TestPhase4Bridge:
    @pytest.mark.asyncio()
    async def test_compose_alert_message_success(self):
        from aegis.llm.bridge import phase4_bridge

        mock_gw = MagicMock()
        mock_gw.complete = AsyncMock(return_value=_make_response("Buy this now before it peaks!"))

        with patch("aegis.llm.bridge.agents_bridge._gateway", mock_gw):
            result = await phase4_bridge.compose_alert_message(
                trend_title="Viral gadget",
                verdict="ENTER",
                score=0.88,
                confidence=0.82,
                priority="P0",
                platforms=["tiktok", "reddit"],
                velocity_1h=55.0,
            )
        assert len(result) > 0

    @pytest.mark.asyncio()
    async def test_compose_alert_fallback(self):
        from aegis.llm.bridge import phase4_bridge
        with patch("aegis.llm.bridge.agents_bridge.get_gateway", side_effect=Exception("x")):
            result = await phase4_bridge.compose_alert_message(
                trend_title="Trend X", verdict="HOLD", score=0.5,
                confidence=0.6, priority="P2", platforms=["reddit"], velocity_1h=5.0
            )
        assert "Trend X" in result

    def test_format_for_telegram(self):
        from aegis.llm.bridge.phase4_bridge import format_alert_for_telegram
        msg = format_alert_for_telegram(
            "Strong opportunity ahead.", "AI Widget", "ENTER", "P0",
            metadata={"score": "0.88"}
        )
        assert "AI Widget" in msg
        assert "ENTER" in msg or "🟢" in msg

    def test_format_for_discord(self):
        from aegis.llm.bridge.phase4_bridge import format_alert_for_discord
        embed_payload = format_alert_for_discord(
            "Strong opportunity.", "AI Widget", "ENTER", "P0"
        )
        assert "embeds" in embed_payload
        assert embed_payload["embeds"][0]["color"] == 0x00C851
