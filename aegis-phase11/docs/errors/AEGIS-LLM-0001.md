# AEGIS-LLM-0001 — All Providers Failed

## Summary
Every LLM provider in the fallback chain has returned an error.
The gateway has exhausted all options.

## Symptoms
```
aegis.llm.errors.AllProvidersFailed: [AEGIS-LLM-0001] All LLM providers failed
```

## Diagnosis

### Step 1 — Check provider health
```bash
aegis llm health
```
Expected: at least one provider shows `✓ healthy`.

### Step 2 — Check Ollama is running
```bash
curl -sf http://localhost:11434/api/version
# Should return: {"version":"0.x.x"}

# If not running:
ollama serve &
```

### Step 3 — Check models are pulled
```bash
ollama list
# Should show at least: qwen2.5:14b, bge-m3

# If empty:
make pull-models
```

### Step 4 — Check Docker service (if using docker-compose)
```bash
docker compose -f docker-compose.phase11.yml ps aegis-ollama
docker compose -f docker-compose.phase11.yml logs aegis-ollama --tail=50
```

### Step 5 — Check cloud keys (if local Ollama is down)
```bash
# Verify at least one cloud key is set:
grep -E "GROQ|OPENROUTER|GEMINI" .env | grep -v "^#" | grep "=."
```

## Resolution

| Root cause | Fix |
|-----------|-----|
| Ollama not running | `ollama serve &` |
| No models pulled | `make pull-models-minimal` |
| All circuit breakers open | Wait 2 minutes for half-open reset |
| No cloud keys and Ollama disabled | Set `AEGIS_DISABLE_OLLAMA=false` in `.env` |
| WSL network issue | `sudo service networking restart` in WSL |

## Prevention
- Run `aegis llm health` as part of your morning startup check.
- Set at least one cloud burst key as emergency fallback.
- Add Uptime Kuma monitor for `http://localhost:11434/api/version`.
