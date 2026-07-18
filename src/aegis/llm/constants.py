"""
aegis.llm.constants — Phase 11 named constants
================================================

Every magic number lives here with a rationale comment.
Import from this module; never scatter literals across the codebase.

Author: AEGIS Engineering
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Provider priority order (0 = highest priority)
# ---------------------------------------------------------------------------

PROVIDER_PRIORITY: dict[str, int] = {
    "ollama": 0,       # Local, zero latency, zero cost, fully private
    "vllm": 1,         # Local GPU server, higher throughput than Ollama
    "groq": 2,         # Free cloud burst; generous RPM on free tier
    "openrouter": 3,   # Free model pool; broader model variety
    "gemini": 4,       # Google free tier; multimodal capability
    "anthropic": 5,    # Paid — high-stakes decisions only
    "openai": 6,       # Paid — last resort
}

# ---------------------------------------------------------------------------
# Timeout budgets (seconds)
# ---------------------------------------------------------------------------

# Hard wall for any single LLM call — prevents runaway coroutines
LLM_HARD_TIMEOUT_S: float = 120.0

# Soft warn threshold — log a warning if response takes longer
LLM_SOFT_WARN_TIMEOUT_S: float = 30.0

# Timeout for provider health-check pings
HEALTH_CHECK_TIMEOUT_S: float = 5.0

# Timeout for semantic router embedding call
SEMANTIC_ROUTER_TIMEOUT_S: float = 2.0

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

# Maximum attempts per provider before circuit trips
MAX_RETRY_ATTEMPTS: int = 3

# Base sleep for decorrelated jitter (seconds)
RETRY_BASE_S: float = 1.0

# Maximum sleep cap between retries (seconds)
RETRY_MAX_S: float = 64.0

# ---------------------------------------------------------------------------
# Circuit breaker thresholds
# ---------------------------------------------------------------------------

# Error rate (0-1) that trips the breaker over a sliding window
CIRCUIT_BREAKER_THRESHOLD: float = 0.30

# Sliding window length for error-rate calculation (seconds)
CIRCUIT_BREAKER_WINDOW_S: float = 60.0

# Time before half-open test after tripping
CIRCUIT_BREAKER_RESET_S: float = 120.0

# ---------------------------------------------------------------------------
# Semantic router
# ---------------------------------------------------------------------------

# Cosine similarity threshold above which a rule-path fires (no LLM needed)
SEMANTIC_ROUTER_THRESHOLD: float = 0.82

# Max tokens used for a router embedding (keeps latency < 5 ms)
SEMANTIC_ROUTER_MAX_TOKENS: int = 128

# ---------------------------------------------------------------------------
# Provider model defaults
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_MODEL: str = "qwen2.5:14b"
OLLAMA_CODER_MODEL: str = "qwen2.5-coder:14b"
OLLAMA_FAST_MODEL: str = "llama3.2:3b"
OLLAMA_EMBED_MODEL: str = "bge-m3"
OLLAMA_RERANK_MODEL: str = "bge-reranker-v2-m3"

VLLM_DEFAULT_MODEL: str = "Qwen/Qwen2.5-14B-Instruct"
VLLM_FAST_MODEL: str = "microsoft/phi-4"

GROQ_DEFAULT_MODEL: str = "llama-3.3-70b-versatile"
GROQ_FAST_MODEL: str = "llama-3.1-8b-instant"

OPENROUTER_DEFAULT_MODEL: str = "mistralai/mistral-small-3"
OPENROUTER_FREE_MODELS: list[str] = [
    "mistralai/mistral-small-3",
    "google/gemma-3-27b-it:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
]

GEMINI_DEFAULT_MODEL: str = "gemini-2.0-flash"
GEMINI_PRO_MODEL: str = "gemini-2.5-pro"

ANTHROPIC_DEFAULT_MODEL: str = "claude-sonnet-4-20250514"
OPENAI_DEFAULT_MODEL: str = "gpt-4.1-mini"

# ---------------------------------------------------------------------------
# Embedding models
# ---------------------------------------------------------------------------

LOCAL_EMBED_MODEL: str = "BAAI/bge-m3"          # Downloaded via sentence-transformers
LOCAL_EMBED_DIM: int = 1024                       # bge-m3 output dimension
LOCAL_EMBED_BATCH_SIZE: int = 32                  # Sentences per batch on CPU

# ---------------------------------------------------------------------------
# Prompt registry
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE_DIR: str = "src/aegis/llm/prompts/templates"
PROMPT_VERSION_HASH_LEN: int = 8  # First N hex chars of sha256 used as version tag

# ---------------------------------------------------------------------------
# Cost tracking (USD per 1M tokens — free tiers cost $0)
# ---------------------------------------------------------------------------

PROVIDER_COST_PER_1M_INPUT: dict[str, float] = {
    "ollama": 0.0,
    "vllm": 0.0,
    "groq": 0.0,       # Free tier
    "openrouter": 0.0, # Free-model path
    "gemini": 0.0,     # Free tier (≤ 15 RPM)
    "anthropic": 3.0,  # claude-sonnet-4; may change
    "openai": 0.40,    # gpt-4.1-mini
}

PROVIDER_COST_PER_1M_OUTPUT: dict[str, float] = {
    "ollama": 0.0,
    "vllm": 0.0,
    "groq": 0.0,
    "openrouter": 0.0,
    "gemini": 0.0,
    "anthropic": 15.0,
    "openai": 1.60,
}

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

# Error code format: AEGIS-LLM-NNNN
ERR_ALL_PROVIDERS_FAILED: str = "AEGIS-LLM-0001"
ERR_PROVIDER_TIMEOUT: str = "AEGIS-LLM-0002"
ERR_GUARDRAIL_BLOCK: str = "AEGIS-LLM-0003"
ERR_INSTRUCTOR_PARSE: str = "AEGIS-LLM-0004"
ERR_CIRCUIT_OPEN: str = "AEGIS-LLM-0005"
ERR_INVALID_PROMPT: str = "AEGIS-LLM-0006"
ERR_EMBED_FAILED: str = "AEGIS-LLM-0007"
ERR_ROUTER_FAILED: str = "AEGIS-LLM-0008"
ERR_PROVIDER_AUTH: str = "AEGIS-LLM-0009"
ERR_CONTEXT_TOO_LONG: str = "AEGIS-LLM-0010"

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

# Maximum output length before guardrail truncation warning fires
GUARDRAIL_MAX_OUTPUT_CHARS: int = 32_000

# Minimum confidence score for structured output to be accepted
GUARDRAIL_MIN_CONFIDENCE: float = 0.0  # Schema validation is the primary gate

# Blocklist patterns for PII leakage detection in LLM outputs
GUARDRAIL_PII_PATTERNS: list[str] = [
    r"\b\d{3}-\d{2}-\d{4}\b",          # SSN
    r"\b\d{16}\b",                       # Credit card (simplistic)
    r"\b[A-Z]{2}\d{6}[A-Z]\b",          # Passport (UK style)
    r"\b\d{10}\b",                        # Phone (10-digit)
]

# ---------------------------------------------------------------------------
# Rate limits (requests per minute) — conservative safe values
# ---------------------------------------------------------------------------

PROVIDER_RPM_LIMITS: dict[str, int] = {
    "ollama": 1000,      # Local — effectively unlimited
    "vllm": 500,         # Local GPU — limited by VRAM
    "groq": 30,          # Free tier limit (may be higher; be conservative)
    "openrouter": 20,    # Free tier limit
    "gemini": 15,        # Gemini free tier strict limit
    "anthropic": 50,     # Tier-1 paid limit
    "openai": 500,       # Tier-1 paid limit
}

# ---------------------------------------------------------------------------
# Eval / golden-answer
# ---------------------------------------------------------------------------

EVAL_GOLDEN_DIR: str = "src/aegis/llm/eval/golden"
EVAL_PASS_RATE_FLOOR: float = 0.90  # CI fails if nightly eval drops below this
