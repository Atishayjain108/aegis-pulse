# AEGIS Pulse — Security Incident Response Runbook

**Version**: 1.0  
**Owner**: AEGIS Engineering  
**Last updated**: 2026-05-19

---

## Severity Levels

| Level | Definition | SLA |
|-------|-----------|-----|
| P0 | Active data breach / secret exposure | Respond in 15 min |
| P1 | Suspected breach / anomalous access | Respond in 1 hour |
| P2 | Security control failure (Vault down, audit gap) | Respond in 4 hours |
| P3 | Security hygiene issues (expired cert, drift) | Respond next business day |

---

## Playbook 1: Secret Exposed in Git

### Indicators
- `gitleaks` CI check fails with a detected secret
- `detect-secrets` finds an audited secret in a new commit
- Someone reports seeing a credential in the repo

### Immediate actions (within 15 minutes)

```bash
# 1. Revoke the exposed secret immediately (do not wait to investigate)
# For Vault tokens:
VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<admin-token> vault token revoke <exposed-token>

# For API keys (Groq, OpenRouter, etc.): revoke via the provider dashboard

# 2. Rotate the secret in Vault
vault kv put secret/aegis/llm groq_api_key="NEW-KEY-FROM-PROVIDER"

# 3. Remove from git history (if in git)
git filter-repo --path-glob '*.env*' --invert-paths  # nuclear option
# OR targeted removal:
git filter-repo --replace-text <(echo "OLD_SECRET==>REMOVED")

# 4. Force-push all branches (coordinate with any collaborators)
git push --force --all

# 5. Invalidate any caches that may have the secret
docker system prune -f
```

### Investigation

```bash
# Check audit log for any use of the exposed secret
grep "auth.token_issued" ~/.aegis/audit.jsonl | jq . | head -50

# Check Vault audit log
VAULT_ADDR=http://127.0.0.1:8200 vault audit list
```

### Post-incident

- Write a postmortem using the template at `docs/postmortem_template.md`
- Add the secret pattern to `.secrets.baseline` (so future occurrences are caught)
- Add a `detect-secrets` pre-commit hook if not already present

---

## Playbook 2: Vault Unreachable

### Indicators
- AEGIS services log `AEGIS-SEC-0003` or `AEGIS-SEC-0005` (circuit breaker open)
- `aegis doctor --secrets` reports Vault disconnected

### Diagnosis

```bash
# Check Vault process
ps aux | grep vault
# Or Docker
docker logs aegis-vault --tail 50

# Check Vault status
vault status
```

### Remediation

```bash
# If running locally (non-Docker):
bash bootstrap/security/01_vault_dev.sh

# If Docker:
docker compose -f docker-compose.yml -f docker-compose.security.yml up -d aegis-vault

# Verify recovery
vault status && echo "Vault is up"
```

### Fallback mode

While Vault is down, `SecretsManager` falls back to SOPS → environment variables.
Confirm services are degraded but functional:

```bash
curl -s http://localhost:8300/api/security/health | jq .vault_connected
# Expected: false (degraded, not unhealthy)
```

---

## Playbook 3: JWT Secret Rotation

### When to rotate
- JWT secret is suspected compromised
- Scheduled rotation (every 90 days in production)

### Steps

```bash
# 1. Generate new secret
NEW_SECRET=$(openssl rand -hex 32)

# 2. Write to Vault
vault kv put secret/aegis/security jwt_secret="${NEW_SECRET}"

# 3. Update environment (rolling restart of services)
# For Docker:
docker compose restart aegis-dashboard aegis-execute-api aegis-predict

# 4. All existing JWTs are now invalid — users must re-login
# No explicit revocation needed; old tokens will fail signature verification
```

### Impact

All active sessions will be invalidated. Inform users if applicable.

---

## Playbook 4: PII Data Breach in Signals Table

### Indicators
- A signal record is found to contain unredacted PII (email, phone, etc.)
- PII scrubber report shows 0 replacements on a signal that should have had PII

### Immediate actions

```bash
# 1. Identify affected records
psql "$AEGIS_PG_DSN" -c "
  SELECT id, platform, title, created_at
  FROM signals
  WHERE title ~* '[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'
  LIMIT 100;
"

# 2. Scrub affected records in-place
python - <<'EOF'
import asyncio
from aegis.security.pii.scrubber import PIIScrubber
# Load and re-scrub affected records
# (implement re-scrub migration in db/migrations/)
EOF

# 3. Check MinIO raw signal archive
# If raw signals in MinIO contain PII, they must be re-scrubbed or deleted
```

### Notification obligations (GDPR / DPDP)

- If EU data subjects are affected: notify within 72 hours (Article 33 GDPR)
- If Indian data subjects affected: notify per DPDP Act requirements
- Document the breach in the audit log:

```bash
# Log the breach event manually
python -c "
import asyncio
from aegis.security.audit.logger import AuditLogger
async def main():
    async with AuditLogger() as log:
        await log.log('security.pii_breach_detected', actor='user:operator',
                      resource='signals', outcome='failure',
                      metadata={'records_affected': N, 'detected_by': 'manual_review'})
asyncio.run(main())
"
```

---

## Playbook 5: Rate Limit Bypass / DoS

### Indicators
- Redis memory pressure growing unexpectedly
- Unusual traffic volume in Prometheus `ingest.signals.rate` metric
- 429s not being returned for high-frequency clients

### Diagnosis

```bash
# Check Redis rate limit keys
redis-cli -u "${AEGIS_REDIS_URL}" KEYS "aegis:ratelimit:*" | head -20

# Check token bucket for a specific IP
redis-cli -u "${AEGIS_REDIS_URL}" HGETALL "aegis:ratelimit:1.2.3.4"
```

### Remediation

```bash
# Manually block an IP at Traefik level (if deployed)
# Or set the rate limit to 1 rpm for a specific IP in Redis:
redis-cli -u "${AEGIS_REDIS_URL}" HSET "aegis:ratelimit:1.2.3.4" tokens 0 last_refill 0

# Permanent block: add IP to deny list in Traefik middleware config
```

---

## Playbook 6: Audit Log Integrity Failure

### Indicators
- `audit_logger.verify_integrity()` returns `is_valid=False`
- HMAC mismatch logged by the verification routine

### Investigation

```bash
python - <<'EOF'
import asyncio
from aegis.security.audit.logger import AuditLogger

async def main():
    logger = AuditLogger()
    valid, count, err = await logger.verify_integrity()
    print(f"Valid: {valid}, checked: {count} entries, error: {err}")

asyncio.run(main())
EOF
```

### Remediation

- If tamper is confirmed: treat as P0 incident; preserve the file as evidence.
- Archive the tampered log to MinIO with object-lock.
- Start a new audit log file (rename the tampered one to `audit.YYYY-MM-DD.tampered.jsonl`).
- Investigate which process had write access to the log file.

---

## Communication Templates

### Internal Slack (P0)

```
🚨 SECURITY INCIDENT P0 — [brief description]
Time: [UTC timestamp]
Affected: [systems]
Status: [investigating / contained / remediated]
Lead: @[engineer]
Bridge: [link]
```

### External notification (if required)

```
Subject: AEGIS Security Notice — [Date]

We detected [brief description] on [date]. [N] records may have been affected.
We have taken the following steps: [actions]. If you have questions, contact [email].
```

---

## Post-Incident Checklist

- [ ] Immediate threat contained
- [ ] Root cause identified
- [ ] Affected users/data enumerated
- [ ] Regulatory notification assessed and filed if required
- [ ] Secret rotated (if applicable)
- [ ] Detection improved (new alert, new pattern in scrubber, etc.)
- [ ] Postmortem written within 48 hours
- [ ] Postmortem shared with team
- [ ] Action items filed as GitHub issues with owners + deadlines
