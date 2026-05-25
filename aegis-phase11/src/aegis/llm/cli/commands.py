"""
aegis.llm.cli — Phase 11 CLI commands
======================================

Adds ``aegis llm`` sub-commands:

  aegis llm health          — print provider health table
  aegis llm complete        — one-shot completion from stdin
  aegis llm embed           — embed text and print vector norm
  aegis llm eval            — run nightly golden-answer eval
  aegis llm pull <model>    — pull a model into Ollama
  aegis llm cost            — print cumulative cost ledger
  aegis llm models          — list available models per provider

Author: AEGIS Engineering
"""

from __future__ import annotations

import asyncio
import json
import sys

import click
import structlog

_log = structlog.get_logger("aegis.llm.cli")


def _run(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine from a sync Click command."""
    return asyncio.run(coro)


@click.group("llm")
def llm_group() -> None:
    """Phase 11 — Local LLM Orchestration commands."""


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@llm_group.command("health")
def cmd_health() -> None:
    """Print provider health table."""

    async def _run_health() -> None:
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway

        gw = await LLMGateway.create(LLMSettings())
        health = await gw.health()
        await gw.aclose()

        click.echo("\n=== LLM Provider Health ===")
        for name, ok in sorted(health.items()):
            status = click.style("✓ healthy", fg="green") if ok else click.style("✗ unreachable", fg="red")
            click.echo(f"  {name:<20} {status}")
        click.echo()

    _run(_run_health())


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


@llm_group.command("complete")
@click.argument("prompt", required=False)
@click.option("--provider", "-p", default=None, help="Force a specific provider")
@click.option("--model", "-m", default=None, help="Override model name")
@click.option("--temperature", "-t", default=0.2, type=float, help="Sampling temperature")
@click.option("--max-tokens", default=1024, type=int, help="Max output tokens")
@click.option("--json-out", is_flag=True, help="Output full response as JSON")
def cmd_complete(
    prompt: str | None,
    *,
    provider: str | None,
    model: str | None,
    temperature: float,
    max_tokens: int,
    json_out: bool,
) -> None:
    """
    One-shot LLM completion.

    PROMPT can be provided as argument or piped via stdin:

    \b
        aegis llm complete "Analyse AI chip trends"
        echo "Analyse AI chip trends" | aegis llm complete
    """
    if prompt is None:
        prompt = sys.stdin.read().strip()

    if not prompt:
        raise click.UsageError("Provide a prompt as argument or via stdin")

    async def _run_complete() -> None:
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway

        gw = await LLMGateway.create(LLMSettings())
        try:
            response = await gw.complete(
                [{"role": "user", "content": prompt}],
                provider=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if json_out:
                click.echo(
                    json.dumps(
                        {
                            "content": response.content,
                            "provider": response.provider,
                            "model": response.model,
                            "latency_ms": response.latency_ms,
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                        },
                        indent=2,
                    )
                )
            else:
                click.echo(f"\n[{response.provider}/{response.model}] {response.latency_ms:.0f}ms\n")
                click.echo(response.content)
        finally:
            await gw.aclose()

    _run(_run_complete())


# ---------------------------------------------------------------------------
# embed
# ---------------------------------------------------------------------------


@llm_group.command("embed")
@click.argument("text")
def cmd_embed(text: str) -> None:
    """Embed TEXT and print vector L2-norm (sanity check)."""

    async def _run_embed() -> None:
        from aegis.llm.config import LLMSettings
        from aegis.llm.gateway.gateway import LLMGateway

        gw = await LLMGateway.create(LLMSettings())
        try:
            vecs = await gw.embed([text])
            vec = vecs[0]
            norm = sum(x * x for x in vec) ** 0.5
            click.echo(f"Dim: {len(vec)}  L2-norm: {norm:.6f}")
            click.echo(f"First 5 dims: {vec[:5]}")
        finally:
            await gw.aclose()

    _run(_run_embed())


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------


@llm_group.command("eval")
@click.option(
    "--golden-dir",
    default="src/aegis/llm/eval/golden",
    help="Path to golden JSONL files",
)
@click.option("--fail-fast", is_flag=True, help="Exit 1 if any case fails")
def cmd_eval(*, golden_dir: str, fail_fast: bool) -> None:
    """Run nightly golden-answer eval suite."""

    async def _run_eval() -> None:
        from aegis.llm.config import LLMSettings
        from aegis.llm.eval.runner import EvalRunner
        from aegis.llm.gateway.gateway import LLMGateway
        from aegis.llm.registry.prompt_registry import PromptRegistry

        cfg = LLMSettings()
        gw = await LLMGateway.create(cfg)
        registry = PromptRegistry(cfg.prompt_template_dir)
        registry.load_all()

        runner = EvalRunner(gateway=gw, registry=registry, golden_dir=golden_dir)
        report = await runner.run_all()
        await gw.aclose()

        click.echo("\n=== Eval Report ===")
        for result in report.results:
            icon = "✓" if result.passed else "✗"
            status = click.style(icon, fg="green" if result.passed else "red")
            click.echo(f"  {status} {result.case.template_name} ({result.latency_ms:.0f}ms)")
            for failure in result.failures:
                click.echo(f"      ↳ {failure}", err=True)

        click.echo(f"\n{report.summary()}\n")

        if fail_fast and not report.is_passing():
            raise SystemExit(1)

    _run(_run_eval())


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


@llm_group.command("pull")
@click.argument("model")
def cmd_pull(model: str) -> None:
    """Pull MODEL into the local Ollama instance."""

    async def _run_pull() -> None:
        from aegis.llm.config import LLMSettings
        from aegis.llm.providers.ollama import OllamaProvider

        cfg = LLMSettings()
        provider = OllamaProvider(base_url=cfg.ollama_base_url)
        try:
            click.echo(f"Pulling {model!r} into Ollama at {cfg.ollama_base_url} ...")
            await provider.pull_model(model)
            click.echo(click.style(f"✓ {model} pulled successfully", fg="green"))
        finally:
            await provider.aclose()

    _run(_run_pull())


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


@llm_group.command("cost")
def cmd_cost() -> None:
    """
    Show cost info per provider.

    Note: live cost tracking requires a running gateway instance.
    This command prints the static cost table from constants.
    """
    from aegis.llm.constants import (
        PROVIDER_COST_PER_1M_INPUT,
        PROVIDER_COST_PER_1M_OUTPUT,
    )

    click.echo("\n=== LLM Cost Table (USD per 1M tokens) ===")
    click.echo(f"  {'Provider':<20} {'Input':<12} {'Output':<12}")
    click.echo("  " + "-" * 44)
    for provider in sorted(PROVIDER_COST_PER_1M_INPUT):
        inp = PROVIDER_COST_PER_1M_INPUT[provider]
        out = PROVIDER_COST_PER_1M_OUTPUT.get(provider, 0.0)
        inp_str = "FREE" if inp == 0 else f"${inp:.2f}"
        out_str = "FREE" if out == 0 else f"${out:.2f}"
        click.echo(f"  {provider:<20} {inp_str:<12} {out_str:<12}")
    click.echo()


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


@llm_group.command("models")
def cmd_models() -> None:
    """List available models for each provider."""

    async def _run_models() -> None:
        from aegis.llm.config import LLMSettings
        from aegis.llm.providers.ollama import OllamaProvider
        from aegis.llm.providers.vllm import VLLMProvider

        cfg = LLMSettings()
        click.echo("\n=== Available Models ===")

        if not cfg.disable_ollama:
            p = OllamaProvider(base_url=cfg.ollama_base_url)
            try:
                models = await p.list_models()
                click.echo(f"\nOllama ({cfg.ollama_base_url}):")
                for m in models:
                    click.echo(f"  - {m}")
                if not models:
                    click.echo("  (none pulled — run: aegis llm pull qwen2.5:14b)")
            finally:
                await p.aclose()

        if cfg.enable_vllm:
            p2 = VLLMProvider(base_url=cfg.vllm_base_url)
            try:
                models = await p2.list_models()
                click.echo(f"\nvLLM ({cfg.vllm_base_url}):")
                for m in models:
                    click.echo(f"  - {m}")
            finally:
                await p2.aclose()

        click.echo()

    _run(_run_models())
