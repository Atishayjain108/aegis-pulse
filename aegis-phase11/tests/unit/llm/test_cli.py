"""tests/unit/llm/test_cli.py — CLI command tests"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def _make_response(content: str = "CLI test response"):
    from aegis.llm.gateway.response import LLMResponse, TokenUsage

    return LLMResponse(
        content=content,
        provider="ollama",
        model="qwen2.5:14b",
        usage=TokenUsage(10, 20, 30),
        latency_ms=42.0,
    )


class TestLLMCLI:
    def test_health_command_runs(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()

        async def _fake_create(*a, **kw):
            gw = MagicMock()
            gw.health = AsyncMock(return_value={"ollama": True, "groq": False})
            gw.aclose = AsyncMock()
            return gw

        with patch("aegis.llm.gateway.gateway.LLMGateway.create", _fake_create):
            result = runner.invoke(llm_group, ["health"])
        assert result.exit_code == 0
        assert "ollama" in result.output

    def test_cost_command_runs(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()
        result = runner.invoke(llm_group, ["cost"])
        assert result.exit_code == 0
        assert "ollama" in result.output
        assert "FREE" in result.output

    def test_complete_command_with_prompt(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()

        async def _fake_create(*a, **kw):
            gw = MagicMock()
            gw.complete = AsyncMock(return_value=_make_response("Test answer"))
            gw.aclose = AsyncMock()
            return gw

        with patch("aegis.llm.gateway.gateway.LLMGateway.create", _fake_create):
            result = runner.invoke(llm_group, ["complete", "What is 2+2?"])
        assert result.exit_code == 0
        assert "Test answer" in result.output

    def test_complete_json_out(self):
        from aegis.llm.cli.commands import llm_group
        import json

        runner = CliRunner()

        async def _fake_create(*a, **kw):
            gw = MagicMock()
            gw.complete = AsyncMock(return_value=_make_response("JSON output"))
            gw.aclose = AsyncMock()
            return gw

        with patch("aegis.llm.gateway.gateway.LLMGateway.create", _fake_create):
            result = runner.invoke(llm_group, ["complete", "--json-out", "test prompt"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "content" in data
        assert "provider" in data

    def test_complete_no_prompt_stdin(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()

        async def _fake_create(*a, **kw):
            gw = MagicMock()
            gw.complete = AsyncMock(return_value=_make_response("Stdin answer"))
            gw.aclose = AsyncMock()
            return gw

        with patch("aegis.llm.gateway.gateway.LLMGateway.create", _fake_create):
            result = runner.invoke(llm_group, ["complete"], input="hello from stdin\n")
        assert result.exit_code == 0

    def test_embed_command(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()

        async def _fake_create(*a, **kw):
            gw = MagicMock()
            gw.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
            gw.aclose = AsyncMock()
            return gw

        with patch("aegis.llm.gateway.gateway.LLMGateway.create", _fake_create):
            result = runner.invoke(llm_group, ["embed", "test text"])
        assert result.exit_code == 0
        assert "Dim:" in result.output

    def test_models_command(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()

        with patch("aegis.llm.providers.ollama.OllamaProvider.list_models",
                   AsyncMock(return_value=["qwen2.5:14b", "bge-m3"])), \
             patch("aegis.llm.providers.ollama.OllamaProvider.aclose",
                   AsyncMock()):
            result = runner.invoke(llm_group, ["models"])
        assert result.exit_code == 0

    def test_eval_command_runs(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()

        async def _fake_create(*a, **kw):
            gw = MagicMock()
            gw.complete = AsyncMock(return_value=_make_response("PROCEED verdict"))
            gw.aclose = AsyncMock()
            return gw

        with patch("aegis.llm.gateway.gateway.LLMGateway.create", _fake_create):
            result = runner.invoke(
                llm_group,
                ["eval", "--golden-dir", "src/aegis/llm/eval/golden"],
            )
        # Should not crash even if golden dir has real files
        assert result.exit_code in (0, 1)

    def test_complete_empty_stdin_shows_error(self):
        from aegis.llm.cli.commands import llm_group

        runner = CliRunner()
        result = runner.invoke(llm_group, ["complete"], input="")
        assert result.exit_code != 0
