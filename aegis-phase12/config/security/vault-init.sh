#!/usr/bin/env sh
# config/security/vault-init.sh
# Vault initialization script executed by the aegis-vault-init Docker sidecar.
# Called after Vault is healthy and ready to accept API requests.
#
# This is a POSIX sh script (not bash) so it runs in the minimal Vault Docker image.

set -e

VAULT_ADDR="${VAULT_ADDR:-http://aegis-vault:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-dev-root-token}"

export VAULT_ADDR VAULT_TOKEN

echo "[vault-init] Waiting for Vault to be ready..."
until vault status > /dev/null 2>&1; do
  sleep 1
done
echo "[vault-init] Vault is ready."

# ── KV v2 ────────────────────────────────────────────────────────────────── #
echo "[vault-init] Enabling KV v2..."
vault secrets enable -version=2 kv 2>/dev/null || echo "[vault-init] KV already enabled"

# ── Transit ──────────────────────────────────────────────────────────────── #
echo "[vault-init] Enabling Transit..."
vault secrets enable transit 2>/dev/null || echo "[vault-init] Transit already enabled"

echo "[vault-init] Creating aegis-key..."
vault write -f transit/keys/aegis-key type=aes256-gcm96 2>/dev/null || \
  echo "[vault-init] aegis-key already exists"

# ── Initial secrets ───────────────────────────────────────────────────────── #
echo "[vault-init] Writing initial AEGIS secrets..."

vault kv put secret/aegis/db \
  password="${AEGIS_DB_PASSWORD:-aegis_app_dev_pw}" \
  host="${AEGIS_DB_HOST:-aegis-postgres}" \
  port="${AEGIS_DB_PORT:-5432}" \
  name="${AEGIS_DB_NAME:-aegis}" \
  user="${AEGIS_DB_USER:-aegis_app}" \
  2>/dev/null && echo "[vault-init] aegis/db written" || true

vault kv put secret/aegis/redis \
  url="${AEGIS_REDIS_URL:-redis://aegis-redis:6379/0}" \
  2>/dev/null && echo "[vault-init] aegis/redis written" || true

vault kv put secret/aegis/minio \
  access_key="${AEGIS_MINIO_ACCESS_KEY:-aegis-dev-key}" \
  secret_key="${AEGIS_MINIO_SECRET_KEY:-aegis-dev-secret-please-change}" \
  endpoint="${AEGIS_MINIO_ENDPOINT:-http://aegis-minio:9000}" \
  2>/dev/null && echo "[vault-init] aegis/minio written" || true

vault kv put secret/aegis/security \
  jwt_secret="${AEGIS_SEC_JWT_SECRET:-CHANGE-ME-generate-with-openssl-rand-hex-32}" \
  hmac_key="${AEGIS_SEC_HMAC_KEY:-CHANGE-ME-generate-with-openssl-rand-hex-32}" \
  2>/dev/null && echo "[vault-init] aegis/security written" || true

vault kv put secret/aegis/llm \
  groq_api_key="${AEGIS_GROQ_API_KEY:-}" \
  openrouter_api_key="${AEGIS_OPENROUTER_API_KEY:-}" \
  gemini_api_key="${AEGIS_GEMINI_API_KEY:-}" \
  2>/dev/null && echo "[vault-init] aegis/llm written" || true

vault kv put secret/aegis/notifiers \
  telegram_bot_token="${AEGIS_TELEGRAM_BOT_TOKEN:-}" \
  discord_webhook_url="${AEGIS_DISCORD_WEBHOOK_URL:-}" \
  ntfy_topic="${AEGIS_NTFY_TOPIC:-aegis-alerts}" \
  2>/dev/null && echo "[vault-init] aegis/notifiers written" || true

# ── Service policies ──────────────────────────────────────────────────────── #
echo "[vault-init] Writing service policies..."

vault policy write aegis-scraper - << 'POLICY'
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/minio" { capabilities = ["read"] }
path "secret/data/aegis/scrape/*" { capabilities = ["read"] }
POLICY
echo "[vault-init] aegis-scraper policy written"

vault policy write aegis-agents - << 'POLICY'
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/llm/*" { capabilities = ["read"] }
path "secret/data/aegis/agents/*" { capabilities = ["read"] }
POLICY
echo "[vault-init] aegis-agents policy written"

vault policy write aegis-predict - << 'POLICY'
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/predict/*" { capabilities = ["read"] }
path "transit/encrypt/aegis-key" { capabilities = ["update"] }
path "transit/decrypt/aegis-key" { capabilities = ["update"] }
POLICY
echo "[vault-init] aegis-predict policy written"

vault policy write aegis-execute - << 'POLICY'
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/execute/*" { capabilities = ["read"] }
path "secret/data/aegis/notifiers/*" { capabilities = ["read"] }
POLICY
echo "[vault-init] aegis-execute policy written"

vault policy write aegis-dashboard - << 'POLICY'
path "secret/data/aegis/db" { capabilities = ["read"] }
path "secret/data/aegis/redis" { capabilities = ["read"] }
path "secret/data/aegis/security/*" { capabilities = ["read"] }
POLICY
echo "[vault-init] aegis-dashboard policy written"

echo "[vault-init] ✔ Vault initialization complete."
echo "[vault-init] UI: ${VAULT_ADDR}/ui  (token: ${VAULT_TOKEN})"
