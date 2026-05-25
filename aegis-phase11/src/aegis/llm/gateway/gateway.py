"""
aegis.llm.gateway.gateway — LLMGateway
=======================================

The single entry point for all LLM interactions across Phase 0–5.

Call graph for a typical request::

    LLMGateway.complete()
        ├── SemanticRouter.route()  ← short-circuit if match
        ├── ProviderSelector.select()  ← ordered provider list
        ├── for provider in ordered:
        │     provider.complete()  ← try each in priority order
        │     break on success
        ├── GuardrailsValidator.validate()  ← check output
        └── return LLMResponse

Thread / coroutine safety:
  The gateway is designed to be shared across the entire process as a
  singleton (one instance per application lifecycle).  All state is either
  immutable or protected by asyncio locks within sub-components.

Author: AEGIS Engineering
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel

from aegis.llm.config import LLMSettings
from aegis.llm.constants import (
    LLM_HARD_TIMEOUT_S,
    PROVIDER_COST_PER_1M_INPUT,
    PROVIDER_COST_PER_1M_OUTPUT,
)
from aegis.llm.errors import AllProvidersFailed
from aegis.llm.gateway.response import LLMResponse, TokenUsage
from aegis.llm.guardrails.validator import GuardrailsValidator
from aegis.llm.providers.base import BaseProvider
from aegis.llm.providers.gemini import GeminiProvider
from aegis.llm.providers.groq import GroqProvider
from aegis.llm.providers.ollama import OllamaProvider
from aegis.llm.providers.openrouter import OpenRouterProvider
from aegis.llm.providers.vllm import VLLMProvider
from aegis.llm.routing.selector import ProviderSelector
from aegis.llm.routing.semantic_router import SemanticRouter

_log = structlog.get_logger("aegis.llm.gateway")

T = TypeVar("T", bound=BaseModel)


class LLMGateway:
    """
    Unified LLM gateway — provider-agnostic, resilient, observable.

    Instantiate once at application startup and share across the process.

    Parameters
    ----------
    settings:
        ``LLMSettings`` instance (reads env vars by default).
    providers:
        Optional explicit provider map; if ``None``, providers are built
        from ``settings`` automatically.
    semantic_router:
        Optional pre-configured ``SemanticRouter`` for fast-path routing.
    guardrails:
        Optional custom ``GuardrailsValidator``; if ``None`` and
        ``settings.enable_guardrails`` is ``True``, a default one is built.

    Example
    -------
    .. code-block:: python

        gw = await LLMGateway.create()
        response = await gw.complete([
            {"role": "user", "content": "Analyse this trend..."}
        ])
        print(response.content)
    """

    def __init__(
        self,
        *,
        settings: LLMSettings,
        providers: dict[str, BaseProvider],
        selector: ProviderSelector,
        guardrails: GuardrailsValidator | None = None,
        semantic_router: SemanticRouter | None = None,
    ) -> None:
        self._settings = settings
        self._providers = providers
        self._selector = selector
        self._guardrails = guardrails
        self._router = semantic_router
        self._cost_ledger: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        settings: LLMSettings | None = None,
        *,
        semantic_router: SemanticRouter | None = None,
    ) -> "LLMGateway":
        """
        Build a fully configured ``LLMGateway`` from settings.

        This is the recommended way to get a gateway instance.

        Parameters
        ----------
        settings:
            Optional settings override; reads from env if ``None``.
        semantic_router:
            Optional pre-compiled ``SemanticRouter``.
        """
        cfg = settings or LLMSettings()

        providers: dict[str, BaseProvider] = {}

        # --- Local providers ------------------------------------------------
        if not cfg.disable_ollama:
            providers["ollama"] = OllamaProvider(
                base_url=cfg.ollama_base_url,
                model=cfg.ollama_model,
                embed_model=cfg.ollama_embed_model,
            )
            _log.info("gateway.provider_registered", provider="ollama", model=cfg.ollama_model)

        if cfg.enable_vllm:
            providers["vllm"] = VLLMProvider(
                base_url=cfg.vllm_base_url,
                model=cfg.vllm_model,
            )
            _log.info("gateway.provider_registered", provider="vllm", model=cfg.vllm_model)

        # --- Cloud providers (only if key configured) -----------------------
        if cfg.groq_api_key:
            providers["groq"] = GroqProvider(
                api_key=cfg.groq_api_key,
                model=cfg.groq_model,
            )
            _log.info("gateway.provider_registered", provider="groq", model=cfg.groq_model)

        if cfg.openrouter_api_key:
            providers["openrouter"] = OpenRouterProvider(
                api_key=cfg.openrouter_api_key,
                model=cfg.openrouter_model,
            )
            _log.info("gateway.provider_registered", provider="openrouter")

        if cfg.gemini_api_key:
            providers["gemini"] = GeminiProvider(
                api_key=cfg.gemini_api_key,
                model=cfg.gemini_model,
            )
            _log.info("gateway.provider_registered", provider="gemini")

        if not providers:
            _log.warning(
                "gateway.no_providers",
                hint="Set AEGIS_DISABLE_OLLAMA=0 or configure at least one cloud API key",
            )

        selector = ProviderSelector(providers=providers)
        guardrails = GuardrailsValidator() if cfg.enable_guardrails else None

        return cls(
            settings=cfg,
            providers=providers,
            selector=selector,
            guardrails=guardrails,
            semantic_router=semantic_router,
        )

    # ------------------------------------------------------------------
    # Core completion API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_s: float = LLM_HARD_TIMEOUT_S,
        skip_router: bool = False,
        skip_guardrails: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Complete a chat conversation.

        Parameters
        ----------
        messages:
            List of ``{"role": str, "content": str}`` dicts.
        provider:
            Force a specific provider (skips selector).
        model:
            Override model name on the selected provider.
        temperature:
            Sampling temperature (defaults to ``settings.llm_temperature``).
        max_tokens:
            Max output tokens (defaults to ``settings.llm_max_tokens``).
        timeout_s:
            Hard timeout for the entire call chain.
        skip_router:
            If ``True``, bypass the semantic router.
        skip_guardrails:
            If ``True``, bypass output guardrails (use for evals only).

        Returns
        -------
        LLMResponse
            Normalised response from whichever provider served the request.

        Raises
        ------
        AllProvidersFailed
            When every provider in the fallback chain fails.
        """
        temperature = temperature if temperature is not None else self._settings.llm_temperature
        max_tokens = max_tokens or self._settings.llm_max_tokens

        t_global_start = time.perf_counter()

        # --- 1. Semantic router fast-path -----------------------------------
        if self._router and not skip_router:
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"),
                "",
            )
            router_result = await self._router.route(last_user)
            if router_result.matched and router_result.response:
                _log.info(
                    "gateway.router_hit",
                    route=router_result.route_name,
                    score=round(router_result.score, 4),
                )
                return LLMResponse(
                    content=router_result.response,
                    provider="semantic_router",
                    model="rule_based",
                    usage=TokenUsage(0, 0, 0),
                    latency_ms=(time.perf_counter() - t_global_start) * 1000,
                    router_short_circuit=True,
                    meta={"route": router_result.route_name},
                )

        # --- 2. Provider selection ------------------------------------------
        if provider:
            if provider not in self._providers:
                raise ValueError(f"Unknown provider {provider!r}")
            ordered = [self._providers[provider]]
        else:
            ordered = await self._selector.select()

        if not ordered:
            raise AllProvidersFailed("No providers available")

        # --- 3. Try each provider in order ----------------------------------
        last_exc: Exception | None = None
        for p in ordered:
            try:
                response = await p.complete(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                    **kwargs,
                )
                # --- 4. Guardrails ------------------------------------------
                if self._guardrails and not skip_guardrails:
                    self._guardrails.validate(response.content)

                # --- 5. Cost tracking ---------------------------------------
                self._record_cost(response)

                _log.info(
                    "gateway.complete",
                    **response.to_log_dict(),
                    total_latency_ms=round((time.perf_counter() - t_global_start) * 1000, 1),
                )
                return response

            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "gateway.provider_failed",
                    provider=p.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                last_exc = exc
                continue

        raise AllProvidersFailed(
            f"All {len(ordered)} provider(s) failed. Last: {last_exc}",
        )

    # ------------------------------------------------------------------
    # Typed structured output (shorthand)
    # ------------------------------------------------------------------

    async def complete_typed(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        provider: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> T:
        """
        Complete and parse into a pydantic model.

        Uses ``InstructorAdapter`` internally; schema instructions are
        automatically appended to the prompt.
        """
        # Import here to avoid circular imports
        from aegis.llm.instructor.adapter import InstructorAdapter

        adapter = InstructorAdapter(gateway=self)
        return await adapter.complete(
            messages,
            schema,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Embedding shorthand
    # ------------------------------------------------------------------

    async def embed(
        self,
        texts: list[str],
        *,
        provider: str = "ollama",
    ) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Currently delegates to the Ollama provider's native embed endpoint.
        Falls back to sentence-transformers if Ollama is unavailable.

        Parameters
        ----------
        texts:
            Input texts to embed.
        provider:
            Provider name (only ``"ollama"`` is currently supported).
        """
        if provider == "ollama" and "ollama" in self._providers:
            p = self._providers["ollama"]
            assert isinstance(p, OllamaProvider)
            return await p.embed(texts)

        # Fallback: sentence-transformers (CPU, no server needed)
        return await self._embed_with_sentence_transformers(texts)

    @staticmethod
    async def _embed_with_sentence_transformers(
        texts: list[str],
    ) -> list[list[float]]:
        """CPU fallback embedding via sentence-transformers."""
        import asyncio

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for CPU-only embedding. "
                "Install with: pip install sentence-transformers"
            ) from exc

        from aegis.llm.constants import LOCAL_EMBED_MODEL

        def _sync_embed() -> list[list[float]]:
            model = SentenceTransformer(LOCAL_EMBED_MODEL)
            vecs = model.encode(texts, normalize_embeddings=True)
            return vecs.tolist()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_embed)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def cost_summary(self) -> dict[str, float]:
        """Return cumulative cost per provider since gateway creation."""
        totals: dict[str, float] = {}
        for entry in self._cost_ledger:
            provider = entry["provider"]
            cost = entry["cost_usd"]
            totals[provider] = totals.get(provider, 0.0) + cost
        return totals

    def _record_cost(self, response: LLMResponse) -> None:
        cost_pair = (
            PROVIDER_COST_PER_1M_INPUT.get(response.provider, 0.0),
            PROVIDER_COST_PER_1M_OUTPUT.get(response.provider, 0.0),
        )
        cost = response.cost_usd({response.provider: cost_pair})
        self._cost_ledger.append(
            {
                "provider": response.provider,
                "model": response.model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cost_usd": cost,
            }
        )

    # ------------------------------------------------------------------
    # Health and lifecycle
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, bool]:
        """Return health status of all registered providers."""
        return await self._selector.all_health()

    async def aclose(self) -> None:
        """Close all provider HTTP clients gracefully."""
        for name, provider in self._providers.items():
            if hasattr(provider, "aclose"):
                try:
                    await provider.aclose()
                except Exception as exc:  # noqa: BLE001
                    _log.warning("gateway.close_error", provider=name, error=str(exc))
        _log.info("gateway.closed")
