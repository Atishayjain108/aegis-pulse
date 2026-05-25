"""
aegis.llm.config — Phase 11 settings
=====================================

All Phase 11 configuration is read from environment variables via
pydantic-settings.  Secrets are never hardcoded.

Provider API keys default to empty string — empty = provider disabled.

Author: AEGIS Engineering
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """
    Phase 11 LLM settings loaded from environment.

    Every field corresponds to an environment variable of the form
    ``AEGIS_<FIELD_NAME_UPPER>``.

    Usage::

        from aegis.llm.config import LLMSettings
        settings = LLMSettings()  # reads from env
    """

    model_config = SettingsConfigDict(
        env_prefix="AEGIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Ollama (local primary)
    # ------------------------------------------------------------------

    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama server base URL",
    )
    ollama_model: str = Field(
        default="qwen2.5:14b",
        description="Default Ollama chat model",
    )
    ollama_embed_model: str = Field(
        default="bge-m3",
        description="Ollama embedding model",
    )
    ollama_coder_model: str = Field(
        default="qwen2.5-coder:14b",
        description="Ollama model for code tasks",
    )
    disable_ollama: bool = Field(
        default=False,
        description="Set True in tests/CI to skip Ollama",
    )

    # ------------------------------------------------------------------
    # vLLM (local GPU secondary)
    # ------------------------------------------------------------------

    vllm_base_url: str = Field(
        default="http://localhost:8001",
        description="vLLM server base URL",
    )
    vllm_model: str = Field(
        default="Qwen/Qwen2.5-14B-Instruct",
        description="Default vLLM model",
    )
    enable_vllm: bool = Field(
        default=False,
        description="Enable vLLM provider (requires GPU server)",
    )

    # ------------------------------------------------------------------
    # Cloud providers (keys default to empty = disabled)
    # ------------------------------------------------------------------

    groq_api_key: str = Field(
        default="",
        description="Groq API key (free tier — sign up at groq.com)",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Default Groq model",
    )

    openrouter_api_key: str = Field(
        default="",
        description="OpenRouter API key (free tier available)",
    )
    openrouter_model: str = Field(
        default="mistralai/mistral-small-3",
        description="Default OpenRouter model",
    )

    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key (free tier at aistudio.google.com)",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Default Gemini model",
    )

    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key (paid — optional)",
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (paid — optional)",
    )

    # ------------------------------------------------------------------
    # Routing and behaviour
    # ------------------------------------------------------------------

    llm_timeout_s: float = Field(
        default=120.0,
        description="Hard timeout for any single LLM call (seconds)",
    )
    llm_temperature: float = Field(
        default=0.2,
        description="Default sampling temperature",
    )
    llm_max_tokens: int = Field(
        default=2048,
        description="Default max output tokens",
    )
    semantic_router_threshold: float = Field(
        default=0.82,
        description="Similarity threshold for semantic router fast-path",
    )
    enable_guardrails: bool = Field(
        default=True,
        description="Enable output guardrails validation",
    )

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    prompt_template_dir: str = Field(
        default="src/aegis/llm/prompts/templates",
        description="Path to Jinja2 prompt template files",
    )

    # ------------------------------------------------------------------
    # Cost tracking
    # ------------------------------------------------------------------

    llm_cost_budget_usd_daily: float = Field(
        default=0.0,
        description="Daily LLM cost budget in USD (0 = unlimited)",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def enabled_providers(self) -> list[str]:
        """Return list of provider names that are configured and enabled."""
        providers: list[str] = []
        if not self.disable_ollama:
            providers.append("ollama")
        if self.enable_vllm:
            providers.append("vllm")
        if self.groq_api_key:
            providers.append("groq")
        if self.openrouter_api_key:
            providers.append("openrouter")
        if self.gemini_api_key:
            providers.append("gemini")
        if self.anthropic_api_key:
            providers.append("anthropic")
        if self.openai_api_key:
            providers.append("openai")
        return providers
