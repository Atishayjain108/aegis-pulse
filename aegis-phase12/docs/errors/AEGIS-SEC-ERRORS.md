# AEGIS-SEC Error Code Reference

All Phase 12 security errors follow the format `AEGIS-SEC-NNNN`.

---

## AEGIS-SEC-0001 — Secret Not Found

**Module**: `aegis.security.vault.client`  
**Trigger**: `VaultClient.read_secret()` called for a path that does not exist in Vault KV v2.

### Diagnosis

```bash
# Check the path exists
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=dev-root-token
vault kv get secret/<your-path>
```

### Remediation

```bash
# Write the missing secret
vault kv put secret/aegis/db password="your-password"
```

---

## AEGIS-SEC-0002 — Permission Denied

**Module**: `aegis.security.vault.client`  
**Trigger**: Vault returned 403 — the token lacks read/write permission on this path.

### Diagnosis

```bash
vault token lookup
vault policy read <policy-name>
```

### Remediation

- Ensure the service token has a policy covering the secret path.
- In dev mode, the root token (`dev-root-token`) has full access.

---

## AEGIS-SEC-0003 — Network Error (Vault Unreachable)

**Module**: `aegis.security.vault.client`  
**Trigger**: All retries exhausted connecting to Vault.

### Diagnosis

```bash
# Check Vault is running
vault status
# Or via Docker
docker logs aegis-vault
```

### Remediation

```bash
# Start Vault in dev mode
bash bootstrap/security/01_vault_dev.sh
# Or via Docker
docker compose -f docker-compose.yml -f docker-compose.security.yml up -d aegis-vault
```

---

## AEGIS-SEC-0005 — Circuit Breaker Open

**Module**: `aegis.security.vault._circuit_breaker`  
**Trigger**: Error rate to Vault exceeded 30% over the last 60 seconds.

### Diagnosis

The circuit breaker will automatically attempt recovery after 120 seconds (half-open).  
Check `aegis-vault` container health and logs.

### Remediation

- Fix the underlying Vault connectivity issue.
- Circuit breaker self-heals; no manual intervention needed if Vault recovers.

---

## AEGIS-SEC-0021 — Secret Key Not Found (SecretsManager)

**Module**: `aegis.security.secrets.manager`  
**Trigger**: `SecretsManager.get()` could not find the key in Vault, SOPS, or environment.

### Diagnosis

```bash
# Check if the environment variable is set
printenv AEGIS_DB_PASSWORD

# Check the SOPS file
sops --decrypt .env.sops.yaml | grep DB_PASSWORD
```

### Remediation

Set the missing variable in your `.env` file or Vault:

```bash
# Environment
export AEGIS_DB_PASSWORD="your-password"

# Or Vault
vault kv put secret/aegis/db password="your-password"
```

---

## AEGIS-SEC-0051 — Missing Authorization Header

**Module**: `aegis.security.rbac.enforcer`  
**Trigger**: Request reached a protected endpoint without a `Bearer` token.

### Remediation

Include the JWT access token in your request:

```bash
curl -H "Authorization: Bearer <access_token>" http://localhost:8300/api/auth/me
```

---

## AEGIS-SEC-0052 — Invalid JWT Token

**Module**: `aegis.security.rbac.enforcer`  
**Trigger**: Token is expired, malformed, or has an invalid signature.

### Remediation

Re-authenticate to get a fresh token:

```bash
curl -X POST http://localhost:8300/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'
```

---

## AEGIS-SEC-0053 — Insufficient Permissions (RBAC)

**Module**: `aegis.security.rbac.enforcer`  
**Trigger**: The authenticated user's role lacks the required permission.

### Diagnosis

Check the permission matrix in `aegis.security.rbac.enforcer`:

| Role     | Permissions include...                          |
|----------|-------------------------------------------------|
| viewer   | read signals, dashboard, alerts, predict        |
| analyst  | + export, batch predict, read audit             |
| operator | + write signals, ack alerts, killswitch control |
| admin    | All permissions                                 |

### Remediation

- Ask an admin to elevate your role, or use an account with sufficient privileges.

---

## AEGIS-SEC-0061 — Wrong Token Type (Access vs Refresh)

**Module**: `aegis.security.tls.jwt_manager`  
**Trigger**: An access token was passed where a refresh token was expected, or vice versa.

### Remediation

Use the correct token:
- `/auth/refresh` requires the **refresh** token.
- All API endpoints require the **access** token.

---

## AEGIS-SEC-0081 — Rate Limit Exceeded

**Module**: `aegis.security.middleware.ratelimit`  
**Trigger**: Client exceeded the requests-per-minute ceiling.

### Response headers

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1716123456
Retry-After: 12
```

### Remediation

Wait for the `Retry-After` seconds before retrying, or contact an admin to increase
your rate limit. Internal services can set `request.state.rate_limit_rpm` to override
per-route.

---

## AEGIS-SEC-0091 — Age Key Already Exists

**Module**: `aegis.security.sops.integration`  
**Trigger**: `generate_age_key()` called when `~/.config/sops/age/keys.txt` already exists.

### Remediation

```python
mgr.generate_age_key(force=True)  # overwrite existing key
```

**WARNING**: Overwriting the key will make existing `.env.sops.yaml` files unreadable.
Re-encrypt all secrets after rotation.

---

## AEGIS-SEC-0092 — age-keygen Not Installed

**Module**: `aegis.security.sops.integration`

### Remediation

```bash
bash bootstrap/security/02_sops_age.sh
# Or manually:
sudo apt install age
```

---

## AEGIS-SEC-0093 — Age Key File Not Found

**Module**: `aegis.security.sops.integration`  
**Trigger**: `get_public_key()` called before `generate_age_key()`.

### Remediation

```bash
bash bootstrap/security/02_sops_age.sh
```

---

## AEGIS-SEC-0095 — SOPS Encrypted File Not Found

**Module**: `aegis.security.sops.integration`  
**Trigger**: `decrypt_to_dict()` called with a non-existent file path.

### Remediation

Ensure the `.env.sops.yaml` file has been committed to the repo and is present
in the current working directory.

---

## AEGIS-SEC-0101 — Invalid Login Credentials

**Module**: `aegis.security.api`  
**Trigger**: `POST /auth/token` with wrong username or password.

### Remediation

Check your credentials. In dev mode, default passwords match usernames (e.g. `admin/admin`).
Never use dev credentials in production — configure proper credentials in Vault.

---

## Quick reference

| Code Range     | Module                        |
|----------------|-------------------------------|
| 0001–0020      | vault.client                  |
| 0021–0030      | secrets.manager               |
| 0031–0040      | pii.scrubber                  |
| 0041–0050      | audit.logger                  |
| 0051–0060      | rbac.enforcer                 |
| 0061–0070      | tls.jwt_manager               |
| 0071–0080      | tls.helpers (mkcert)          |
| 0081–0090      | middleware.ratelimit           |
| 0091–0100      | sops.integration              |
| 0101–0120      | api (auth routes)             |
