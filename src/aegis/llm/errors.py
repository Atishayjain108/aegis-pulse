"""
aegis.llm.errors — Phase 11 typed error hierarchy
===================================================

Every error has:
  - A machine-readable code (``AEGIS-LLM-NNNN``)
  - A human-readable message
  - A ``docs_url`` pointing to the error's runbook

Author: AEGIS Engineering
"""

from __future__ import annotations

from aegis.llm.constants import (
    ERR_ALL_PROVIDERS_FAILED,
    ERR_CIRCUIT_OPEN,
    ERR_CONTEXT_TOO_LONG,
    ERR_EMBED_FAILED,
    ERR_GUARDRAIL_BLOCK,
    ERR_INSTRUCTOR_PARSE,
    ERR_INVALID_PROMPT,
    ERR_PROVIDER_AUTH,
    ERR_PROVIDER_TIMEOUT,
    ERR_ROUTER_FAILED,
)

_DOCS_BASE = "https://aegis.internal/docs/errors"


class AegisLLMError(Exception):
    """Base class for all Phase 11 errors."""

    code: str = "AEGIS-LLM-0000"
    message: str = "Unknown LLM error"

    def __init__(self, detail: str = "", *, provider: str = "") -> None:
        self.detail = detail
        self.provider = provider
        self.docs_url = f"{_DOCS_BASE}/{self.code}.md"
        super().__init__(f"[{self.code}] {self.message}: {detail}")

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code!r}, provider={self.provider!r}, detail={self.detail!r})"
        )


class AllProvidersFailed(AegisLLMError):
    """Raised when every provider in the fallback chain has failed."""

    code = ERR_ALL_PROVIDERS_FAILED
    message = "All LLM providers failed"


class ProviderTimeout(AegisLLMError):
    """Raised when a provider exceeds its hard timeout budget."""

    code = ERR_PROVIDER_TIMEOUT
    message = "Provider timed out"


class GuardrailBlock(AegisLLMError):
    """Raised when the output guardrail rejects a response."""

    code = ERR_GUARDRAIL_BLOCK
    message = "Output blocked by guardrail policy"

    def __init__(self, detail: str = "", *, rule: str = "", provider: str = "") -> None:
        super().__init__(detail, provider=provider)
        self.rule = rule


class InstructorParseError(AegisLLMError):
    """Raised when pydantic-instructor cannot parse the LLM output into the target schema."""

    code = ERR_INSTRUCTOR_PARSE
    message = "Structured output parse failure"

    def __init__(self, detail: str = "", *, schema: str = "", provider: str = "") -> None:
        super().__init__(detail, provider=provider)
        self.schema = schema


class CircuitOpen(AegisLLMError):
    """Raised when a provider's circuit breaker is open."""

    code = ERR_CIRCUIT_OPEN
    message = "Provider circuit breaker is open"


class InvalidPrompt(AegisLLMError):
    """Raised when a prompt template fails rendering or validation."""

    code = ERR_INVALID_PROMPT
    message = "Prompt template invalid"


class EmbedFailed(AegisLLMError):
    """Raised when the embedding pipeline fails."""

    code = ERR_EMBED_FAILED
    message = "Embedding generation failed"


class RouterFailed(AegisLLMError):
    """Raised when the semantic router cannot classify a query."""

    code = ERR_ROUTER_FAILED
    message = "Semantic router failed"


class ProviderAuthError(AegisLLMError):
    """Raised on 401/403 from a provider — bad API key or expired token."""

    code = ERR_PROVIDER_AUTH
    message = "Provider authentication failed"


class ContextTooLong(AegisLLMError):
    """Raised when the rendered prompt exceeds the provider's context window."""

    code = ERR_CONTEXT_TOO_LONG
    message = "Prompt exceeds provider context limit"

    def __init__(
        self,
        detail: str = "",
        *,
        token_count: int = 0,
        context_limit: int = 0,
        provider: str = "",
    ) -> None:
        super().__init__(detail, provider=provider)
        self.token_count = token_count
        self.context_limit = context_limit
