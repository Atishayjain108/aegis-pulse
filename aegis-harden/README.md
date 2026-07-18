# AEGIS Pulse — Phase 5: Adversarial Hardening

> Defensive controls for the AEGIS Pulse stack. Covers TLS/HTTP-2 fingerprint
> diversity, per-source YAML playbooks, honeypot avoidance, randomized-smoothing
> ML defense, and data-poisoning detection.

**Status:** Green. 187/187 tests pass (180 unit + 7 integration). 94% coverage.

---

## What this phase is for

Phase 5 is a *library* layered over Phases 0–4, not a service:

| Layer | Where Phase 5 plugs in |
|---|---|
| **Phase 1 — scraper** | Fingerprint pool, proxy posture, per-source playbook, honeypot URL/DOM screen |
| **Phase 3 — predict** | Randomized-smoothing wrapper, data-poisoning detectors on training batches |
| **Phase 4 — execute** | `HardenVerdict` payloads published to `aegis:phase5:verdicts` Redis stream — merged by Phase 4 intake the same way Phase 2/3 results are |

---

## Doctrine — same as the rest of the project

* **Heuristic-first.** Every detector produces a deterministic verdict from
  cheap features. ML augments, never flips.
* **No hidden global state.** All decisions are reproducible from a `SeededRng`.
* **Graceful degradation.** Optional deps (`prometheus_client`, `scikit-learn`)
  fall back to no-ops; the suite still passes.
* **Frozen Pydantic v2 schemas.** `Playbook`, `FingerprintProfile`,
  `HoneypotVerdict`, `SmoothingResult`, `PoisoningReport`, `HardenVerdict`.

---

## Layout

```
aegis-phase5/
  src/aegis/harden/
    __init__.py
    constants.py        — all magic numbers w/ rationale
    errors.py           — AEGIS-HARDEN-NNNN typed codes
    schemas.py          — Pydantic v2 frozen models
    config/             — pydantic-settings (AEGIS_HARDEN_*)
    utils/              — clock, RNG, structlog
    metrics/            — Prometheus M.<…> with no-op fallback
    fingerprint/        — JA3/JA4 + H2 fingerprint pool
    playbooks/          — YAML loader, registry, matching
    honeypot/           — URL + DOM detectors
    smoothing/          — Cohen-style randomized smoothing
    poisoning/          — label-flip / feature-shift / gradient-anomaly
    proxy/              — proxy rotation policy
    detect/             — facade: internal verdicts → HardenVerdict
    bridge_phase4.py    — XADD payload conforming to Phase 4 intake
    cli/                — `aegis-harden` Typer CLI
  playbooks/            — bundled YAMLs (default + 4 sources)
  tests/                — 187 tests
  docker/
    Dockerfile          — multi-stage, tini PID1, non-root, healthcheck
    compose-snippet.yml — merge into main docker-compose.yml
```

---

## Quickstart

```bash
# Install (dev)
uv sync --all-packages --all-extras

# Run the full Phase 5 suite
uv run --package aegis-harden python -m pytest tests/ -q

# Sanity check the environment
uv run --package aegis-harden aegis-harden doctor

# Validate a playbook directory
uv run --package aegis-harden aegis-harden playbooks validate playbooks/

# See which playbook matches a source/URL
uv run --package aegis-harden aegis-harden playbooks match --source reddit-rss
uv run --package aegis-harden aegis-harden playbooks match --url https://amazon.com/x

# Score a URL for honeypot signals
uv run --package aegis-harden aegis-harden honeypot scan-url \
    https://example.com/donotvisit

# Smoothing demo (synthetic predictor)
uv run --package aegis-harden aegis-harden smooth demo --samples 128
```

---

## Public API surface

### Phase 1 integration

```python
from aegis.harden.fingerprint import FingerprintPool
from aegis.harden.playbooks   import builtin_registry, load_dir
from aegis.harden.proxy       import ProxyPosture
from aegis.harden.detect      import screen_url, screen_dom_element
from aegis.harden.utils       import SeededRng

pool       = FingerprintPool()
registry   = load_dir(Path("playbooks/")) or builtin_registry()
rng        = SeededRng(seed=...)
posture    = ProxyPosture(pool_size=len(my_proxy_pool))

playbook   = registry.match(source="reddit-rss")
profile    = pool.pick(rng)
proxy      = posture.decide(playbook=playbook, request_seq=n, rng=rng)
verdict    = screen_url(target_url)
if verdict.verdict == "block":
    skip_url(target_url, reason=verdict.reason_code)
```

### Phase 3 integration

```python
from aegis.harden.detect import screen_inference, screen_training_batch

# Inference defense — wrap a Phase 3 single-vector predictor.
def base(x):
    return runner.infer_one(FeatureWindow.from_vector(x)).p_breakout_at(24)

v = screen_inference(base, feature_vector, sigma=0.05, n_samples=128)
# v.verdict ∈ {proceed, warn, block}; "warn" means smoothing disagrees w/ raw.

# Training-time defense — scan a batch before training.
v = screen_training_batch(
    x=batch_features,
    labels=batch_labels,
    reference_labels=heuristic_floor_labels(batch_features),
    x_ref=previous_window_features,
    batch_id="2026Q2-w17",
)
if v.verdict == "block":
    raise BatchRejected(v.detail)
```

### Phase 4 integration

```python
from aegis.harden.bridge_phase4 import publish
import redis.asyncio as redis

client = redis.from_url("redis://aegis-redis:6379/0")
await publish(client, "aegis:phase5:verdicts", harden_verdict, maxlen=10_000)
```

The payload conforms to the same intake shape Phase 4 expects from Phase 2
(see CLAUDE.md). The bridge maps `proceed/warn/block` → `ENTER/HOLD/BLOCK`
identically to the existing Phase 2 mapping.

---

## Config (env vars)

| Var | Default | Notes |
|---|---|---|
| `AEGIS_HARDEN_DEFAULT_PROFILE` | `standard` | `minimal` \| `standard` \| `stealth` \| `tor` |
| `AEGIS_HARDEN_PLAYBOOKS_DIR` | `playbooks` | dir of `*.yaml` playbooks |
| `AEGIS_HARDEN_JA3_POOL_PATH` | unset | optional JSON pool to merge with builtins |
| `AEGIS_HARDEN_JA4_POOL_PATH` | unset | optional JSON pool |
| `AEGIS_HARDEN_SMOOTHING_DEFAULT_SAMPLES` | `128` | bounds: 16 ≤ n ≤ 4096 |
| `AEGIS_HARDEN_SMOOTHING_DEFAULT_SIGMA` | `0.10` | bounds: 0 ≤ σ ≤ 1 |
| `AEGIS_HARDEN_REDIS_URL` | `redis://localhost:6380/0` | Phase 4 publish path |
| `AEGIS_HARDEN_VERDICT_STREAM` | `aegis:phase5:verdicts` | Phase 4 stream name |
| `AEGIS_HARDEN_PUBLISH_TO_REDIS` | `false` | toggle bridge in callers |
| `AEGIS_HARDEN_RNG_SEED` | `0xA5615` | reproducibility seed |

---

## Error codes

All Phase 5 typed errors carry an `AEGIS-HARDEN-NNNN` code. See `errors.py` for
the codebook; runbook fragments live in `docs/phase5/errors/`.

| Code | Class | Meaning |
|---|---|---|
| 0001 | PlaybookValidationError | Missing required key |
| 0002 | PlaybookValidationError | Field out of bounds |
| 0003 | PlaybookValidationError | Unknown profile |
| 0004 | PlaybookValidationError | No playbook matched |
| 0010 | FingerprintPoolError | TLS pool empty/corrupt |
| 0011 | FingerprintPoolError | JA4 pool empty/corrupt |
| 0012 | FingerprintPoolError | HTTP/2 settings out of bounds |
| 0020 | HoneypotBlocked | Honeypot pre-click |
| 0021 | HoneypotBlocked | URL-pattern honeypot |
| 0030 | SmoothingError | Bad input shape |
| 0031 | SmoothingError | Parameter out of bounds |
| 0032 | SmoothingError | Sampler returned NaN |
| 0040 | PoisoningDetected | Label-flip exceeds threshold |
| 0041 | PoisoningDetected | Feature shift exceeds threshold |
| 0042 | PoisoningDetected | Sample count below floor |

---

## Tests

```
180 unit tests + 7 integration tests = 187 passing
Coverage: 94% line coverage on `aegis.harden`
```

Run with:

```bash
PYTHONPATH=src python -m pytest tests/ --cov=aegis.harden
```

---

## Verifying integration with Phases 1–4 (no external services required)

```bash
# 1. The CLI doctor sanity-checks every Phase 5 subsystem.
aegis-harden doctor

# 2. The integration test suite exercises the cross-phase flow end-to-end:
python -m pytest tests/integration -v
```

The integration suite uses fakes for Redis; no infrastructure is required.
For full live-stack verification, set `AEGIS_HARDEN_PUBLISH_TO_REDIS=true`
and run the main stack with `docker compose up -d`.
