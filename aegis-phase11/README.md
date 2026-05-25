# AEGIS Pulse — Phase 11: Local LLM Orchestration

> **Zero paid APIs required.** Phase 11 runs end-to-end on a local Ollama
> server. Cloud providers (Groq, OpenRouter, Gemini) are optional burst paths.

---

## Architecture

```
Caller (Phase 2 agent node, CLI, API)
        │
        ▼
┌───────────────────────────────┐
│         LLMGateway            │  ← single entry point
│  ┌──────────────────────────┐ │
│  │    SemanticRouter        │ │  ← short-circuit for simple queries
│  └──────────────────────────┘ │
│  ┌──────────────────────────┐ │
│  │    ProviderSelector      │ │  ← health-aware priority ordering
│  └──────────────────────────┘ │
│  ┌──────────────────────────┐ │
│  │  Provider Fallback Chain │ │
│  │  0. Ollama (local, free) │ │
│  │  1. vLLM  (local GPU)   │ │
│  │  2. Groq  (free cloud)  │ │
│  │  3. OpenRouter (free)   │ │
│  │  4. Gemini (free)       │ │
│  │  5. Anthropic (paid)    │ │
│  └──────────────────────────┘ │
│  ┌──────────────────────────┐ │
│  │  GuardrailsValidator     │ │  ← PII, toxic, length checks
│  └──────────────────────────┘ │
└───────────────────────────────┘
        │
        ▼
   LLMResponse (provider-agnostic)
```

---

## Quick Start

### 1. Install Ollama (required — primary path)

```bash
# Inside WSL2 Ubuntu 24.04
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &

# Pull the minimum model zoo
make pull-models-minimal     # just llama3.2:3b (~2 GB)
# OR the full zoo:
make pull-models             # qwen2.5:14b + coder + bge-m3 (~20 GB total)
```

### 2. Install Python dependencies

```bash
uv sync --all-extras
```

### 3. Configure (copy and edit)

```bash
cp .env.example .env
# Edit .env — only AEGIS_OLLAMA_BASE_URL is required for local-only mode
# Add AEGIS_GROQ_API_KEY etc. for cloud burst (all free tier)
```

### 4. Health check

```bash
make health
# OR
aegis llm health
```

Expected output:
```
=== LLM Provider Health ===
  ollama               ✓ healthy
  groq                 ✗ unreachable   ← normal if key not set
```

### 5. Test a completion

```bash
aegis llm complete "What are the top 3 emerging e-commerce trends in 2026?"
```

---

## Integration with Phase 2 Agents

Replace the legacy LLM call in any agent node:

**Before (legacy):**
```python
from aegis.agents.llm.router import get_llm_response
result = await get_llm_response(prompt)
```

**After (Phase 11):**
```python
from aegis.llm.bridge.agents_bridge import complete_for_agent
result = await complete_for_agent("scout", messages)
```

Or for typed structured output:
```python
from aegis.llm.bridge.agents_bridge import get_gateway
from pydantic import BaseModel

class Verdict(BaseModel):
    decision: str
    confidence: float

gw = await get_gateway()
verdict = await gw.complete_typed(messages, Verdict)
print(verdict.decision)  # "PROCEED"
```

---

## Provider Configuration

| Provider | Env Var | Free Tier | Notes |
|----------|---------|-----------|-------|
| Ollama (local) | `AEGIS_OLLAMA_BASE_URL` | ✅ Unlimited | Primary path |
| vLLM (local GPU) | `AEGIS_VLLM_BASE_URL` | ✅ Unlimited | Requires GPU |
| Groq | `AEGIS_GROQ_API_KEY` | ✅ ~14k req/day | Sign up at groq.com |
| OpenRouter | `AEGIS_OPENROUTER_API_KEY` | ✅ Free models | openrouter.ai |
| Gemini | `AEGIS_GEMINI_API_KEY` | ✅ 15 RPM | aistudio.google.com |
| Anthropic | `AEGIS_ANTHROPIC_API_KEY` | ❌ Paid | High-stakes only |
| OpenAI | `AEGIS_OPENAI_API_KEY` | ❌ Paid | Last resort |

---

## Semantic Router

Add fast-path routes to avoid LLM calls for simple queries:

```python
from aegis.llm.routing.semantic_router import Route, SemanticRouter

router = SemanticRouter(
    routes=[
        Route(
            name="health_check",
            utterances=["are you alive", "ping", "system status", "health"],
            response="All AEGIS systems operational.",
        ),
        Route(
            name="help",
            utterances=["what can you do", "help me", "commands available"],
            response="I can analyse trends, score opportunities, and generate alerts.",
        ),
    ],
    embed_fn=gateway.embed,  # Use existing gateway embed method
)
await router.compile()

# Wire into gateway at creation time
gw = await LLMGateway.create(settings, semantic_router=router)
```

---

## Prompt Templates

All prompts live in `src/aegis/llm/prompts/templates/*.jinja2`.

Every template requires a YAML front-matter block:

```jinja2
---
name: my_template
version: 1
required_vars: [topic, signal_count]
description: "Template for analysing topics"
---
Analyse {{ topic }} with {{ signal_count }} signals.
```

Render via the registry:

```python
from aegis.llm.registry.prompt_registry import PromptRegistry

registry = PromptRegistry("src/aegis/llm/prompts/templates")
registry.load_all()
rendered = registry.render("my_template", topic="AI chips", signal_count=500)
```

---

## Guardrails

Custom validation rules:

```python
from aegis.llm.guardrails.validator import GuardrailsValidator, ValidationResult

def block_competitor_mentions(output: str, ctx: dict) -> ValidationResult:
    if "CompetitorName" in output:
        return ValidationResult(
            passed=False,
            rule="competitor_mention",
            detail="Output mentions a competitor",
        )
    return ValidationResult(passed=True, rule="competitor_mention")

validator = GuardrailsValidator(custom_validators=[block_competitor_mentions])
```

---

## Nightly Eval

Golden answer files in `src/aegis/llm/eval/golden/<template_name>.jsonl`:

```jsonl
{"input_vars": {"trend_title": "AI chips", "signal_count": 850, ...}, "expected_contains": ["PROCEED"], "expected_not_contains": ["BLOCK"]}
```

Run eval:
```bash
make eval
# OR in CI:
aegis llm eval --fail-fast
```

---

## CLI Reference

```bash
aegis llm health              # Provider health table
aegis llm complete "prompt"   # One-shot completion
aegis llm complete --provider groq "prompt"
aegis llm embed "text"        # Embed and show vector norm
aegis llm eval                # Run golden-answer eval suite
aegis llm pull qwen2.5:14b    # Pull model into Ollama
aegis llm models              # List available models
aegis llm cost                # Cost table per provider
```

---

## Testing

```bash
make test           # Full suite with coverage (floor: 82%)
make test-fast      # Quick smoke tests (no embed/model pull)
make lint           # Ruff lint
make type           # mypy --strict
```

---

## Error Codes

| Code | Meaning | Fix |
|------|---------|-----|
| AEGIS-LLM-0001 | All providers failed | Check `aegis llm health`; ensure Ollama is running |
| AEGIS-LLM-0002 | Provider timeout | Increase `AEGIS_LLM_TIMEOUT_S` or check provider load |
| AEGIS-LLM-0003 | Guardrail block | Output failed safety validation; review prompt |
| AEGIS-LLM-0004 | Structured parse failure | Add more JSON examples to prompt; lower temperature |
| AEGIS-LLM-0005 | Circuit breaker open | Provider repeatedly failing; check logs |
| AEGIS-LLM-0006 | Invalid prompt | Check template front-matter and required_vars |
| AEGIS-LLM-0007 | Embed failed | Check Ollama bge-m3 model is pulled |
| AEGIS-LLM-0008 | Router failed | Embedding service unavailable |
| AEGIS-LLM-0009 | Auth failed | Check API key in .env |
| AEGIS-LLM-0010 | Context too long | Reduce prompt size or switch to higher-context model |

---

## File Layout

```
src/aegis/llm/
  __init__.py              Package root; exports LLMGateway, LLMResponse
  constants.py             Named constants (no magic numbers)
  errors.py                Typed error hierarchy (AEGIS-LLM-NNNN)
  config.py                pydantic-settings LLMSettings
  gateway/
    gateway.py             LLMGateway — single entry point
    response.py            LLMResponse, TokenUsage dataclasses
  providers/
    base.py                BaseProvider + circuit breaker + retry
    ollama.py              Ollama adapter (primary, local, free)
    vllm.py                vLLM adapter (GPU, local, free)
    groq.py                Groq adapter (free cloud burst)
    openrouter.py          OpenRouter adapter (free cloud burst)
    gemini.py              Gemini adapter (free cloud burst)
  routing/
    selector.py            ProviderSelector (health-aware ordering)
    semantic_router.py     SemanticRouter (LLM bypass for simple queries)
  guardrails/
    validator.py           GuardrailsValidator (PII, toxic, length)
  instructor/
    adapter.py             InstructorAdapter (typed pydantic output)
  registry/
    prompt_registry.py     PromptRegistry (Jinja2 templates + versioning)
  bridge/
    agents_bridge.py       Phase 2 ↔ Phase 11 integration seam
  eval/
    runner.py              EvalRunner (nightly golden-answer tests)
    golden/                Golden JSONL files per template
  prompts/
    templates/             Jinja2 prompt templates
  cli/
    commands.py            aegis llm * CLI commands
bootstrap/
  wsl/06_ollama_models.sh  Idempotent model pull script
config/
  litellm_config.yaml      LiteLLM proxy configuration
docker-compose.phase11.yml Phase 11 Docker services
Makefile                   Developer UX targets
```
