# AEGIS Phase 11 — Integration Guide

## How Phase 11 connects to Phases 0–4

### Phase 2 (Agents) → Phase 11

All 10 agent nodes use `complete_for_agent()` from the bridge:

```python
# In any agent node file (e.g. src/aegis/agents/nodes/scout.py)
from aegis.llm.bridge.agents_bridge import complete_for_agent

async def _call_llm(state: GraphState, prompt: str) -> str:
    return await complete_for_agent(
        "scout",
        [
            {"role": "system", "content": SCOUT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1024,
    )
```

For typed structured output (replaces manual JSON parsing):

```python
from aegis.llm.bridge.agents_bridge import get_gateway
from aegis.llm.instructor.schemas import ScoutOutput

async def _call_llm_typed(messages: list) -> ScoutOutput:
    gw = await get_gateway()
    return await gw.complete_typed(messages, ScoutOutput, temperature=0.1)
```

### Phase 3 (Predict) → Phase 11

Phase 3 calls the bridge for LLM-enhanced rationale generation:

```python
# In src/aegis/predict/inference/runner.py
from aegis.llm.bridge.phase3_bridge import generate_prediction_rationale

rationale = await generate_prediction_rationale(
    trend_title=candidate.title,
    verdict=prediction.verdict,
    p_breakout=prediction.p_breakout_24h,
    p_decline=prediction.p_decline_6h,
    confidence=prediction.confidence,
    horizon_h=24,
    velocity_1h=candidate.velocity_1h,
    sentiment=candidate.sentiment,
    novelty=candidate.novelty,
)
prediction = prediction.model_copy(update={"rationale": rationale})
```

### Phase 4 (Execute) → Phase 11

Phase 4 uses the bridge for alert message composition:

```python
# In aegis-phase4/src/aegis/execute/pipeline/composer.py
from aegis.llm.bridge.phase4_bridge import compose_alert_message, format_alert_for_telegram

alert_text = await compose_alert_message(
    trend_title=input_data.title,
    verdict=input_data.verdict,
    score=input_data.score,
    confidence=input_data.confidence,
    priority=input_data.priority,
    platforms=input_data.platforms,
    velocity_1h=input_data.velocity_1h,
)
telegram_msg = format_alert_for_telegram(alert_text, input_data.title, input_data.verdict, "P0")
```

---

## Initialisation at startup

Add to your application startup code:

```python
# In src/aegis/cli/__init__.py or app startup
import asyncio
from aegis.llm.bridge.agents_bridge import get_gateway, close_gateway

async def startup():
    gw = await get_gateway()
    health = await gw.health()
    print(f"LLM providers: {health}")

async def shutdown():
    await close_gateway()
```

---

## Using the prompt registry

```python
from aegis.llm.prompts import render

# Render any template by name
prompt = render(
    "scout_analysis",
    trend_title="AI chips",
    signal_count=850,
    platforms=["reddit", "tiktok", "amazon"],
    velocity_1h=42.5,
    sentiment=0.72,
    commercial_intent=0.81,
)
```

---

## Enabling caching

```python
from aegis.llm.cache import LLMCache
from aegis.llm.gateway.gateway import LLMGateway

cache = LLMCache(
    redis_url="redis://localhost:6380/0",
    ttl_s=3600,
)

# Wrap completion calls with cache check
async def cached_complete(gw: LLMGateway, messages: list) -> str:
    cached = await cache.get(messages, temperature=0.2)
    if cached:
        return cached.content
    response = await gw.complete(messages, temperature=0.2)
    await cache.set(messages, response, temperature=0.2)
    return response.content
```

---

## Enabling streaming in FastAPI endpoints

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from aegis.llm.gateway.streaming import stream_complete
from aegis.llm.bridge.agents_bridge import get_gateway

app = FastAPI()

@app.get("/stream")
async def stream_endpoint(prompt: str):
    gw = await get_gateway()

    async def event_generator():
        async for chunk in stream_complete(gw, [{"role": "user", "content": prompt}]):
            yield f"data: {chunk.delta}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

## Wiring PII scrubbing before LLM calls

```python
from aegis.llm.guardrails.pii_scrubber import PIIScrubber
from aegis.llm.bridge.agents_bridge import get_gateway

scrubber = PIIScrubber()

async def safe_complete(messages: list[dict]) -> str:
    # Scrub PII from user messages before sending to any LLM
    clean_messages, scrub_result = scrubber.scrub_messages(messages)
    if scrub_result.was_modified:
        logger.warning("PII scrubbed before LLM call", total=scrub_result.total_replacements)

    gw = await get_gateway()
    response = await gw.complete(clean_messages)
    return response.content
```

---

## Context window pre-check

```python
from aegis.llm.tokenizer import fits_in_context, truncate_messages

async def complete_safely(gw, messages: list[dict], provider_context: int = 32768) -> str:
    if not fits_in_context(messages, context_limit=provider_context):
        messages = truncate_messages(messages, context_limit=provider_context)

    response = await gw.complete(messages)
    return response.content
```

---

## Environment variables quick reference

```bash
# Minimum required (local-only mode)
AEGIS_OLLAMA_BASE_URL=http://localhost:11434
AEGIS_DISABLE_OLLAMA=false

# Free cloud burst (recommended)
AEGIS_GROQ_API_KEY=gsk_...
AEGIS_GEMINI_API_KEY=AIza...
AEGIS_OPENROUTER_API_KEY=sk-or-...

# Behaviour tuning
AEGIS_LLM_TIMEOUT_S=120
AEGIS_LLM_TEMPERATURE=0.2
AEGIS_ENABLE_GUARDRAILS=true
AEGIS_SEMANTIC_ROUTER_THRESHOLD=0.82
```
