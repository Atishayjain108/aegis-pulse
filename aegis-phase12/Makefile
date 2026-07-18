# ============================================================
# AEGIS Pulse — Phase 12 Security Makefile
# ============================================================
# Usage:
#   make help           — show all targets
#   make install        — install all dependencies
#   make test           — run all tests
#   make test-fast      — unit tests only (no integration)
#   make lint           — run ruff linter
#   make type           — run mypy type checker
#   make security-scan  — run secret scanner
#   make coverage       — test + coverage report
#   make doctor         — run security health check
#   make vault-init     — bootstrap Vault dev mode
#   make clean          — remove build artifacts
# ============================================================

PYTHON       := python3
PYTEST       := python -m pytest
PYTHONPATH   := src
RUFF         := python -m ruff
MYPY         := python -m mypy
COVERAGE_MIN := 82

.PHONY: help install install-dev test test-fast test-integration \
        lint lint-fix type coverage security-scan bandit \
        doctor vault-init vault-status rotate-jwt \
        audit-verify audit-tail pii-test \
        clean clean-pyc clean-coverage \
        pre-commit-install pre-commit-run \
        docker-security-up docker-security-down \
        build package

# ── Default target ─────────────────────────────────────────────────────────── #

.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "AEGIS Pulse — Phase 12 Security"
	@echo "================================"
	@echo ""
	@echo "Development:"
	@echo "  make install          Install all dependencies (uv)"
	@echo "  make install-dev      Install with dev extras"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run all tests (unit + integration)"
	@echo "  make test-fast        Run unit tests only (no infra needed)"
	@echo "  make test-integration Run integration tests (requires running services)"
	@echo "  make coverage         Run tests with coverage report (min $(COVERAGE_MIN)%)"
	@echo ""
	@echo "Code quality:"
	@echo "  make lint             Run ruff linter"
	@echo "  make lint-fix         Run ruff linter with auto-fix"
	@echo "  make type             Run mypy type checker"
	@echo "  make bandit           Run bandit security linter"
	@echo "  make security-scan    Scan repo for leaked secrets (detect-secrets)"
	@echo "  make pre-commit-run   Run all pre-commit hooks"
	@echo ""
	@echo "Security operations:"
	@echo "  make doctor           Run security health check"
	@echo "  make vault-init       Bootstrap Vault with AEGIS config"
	@echo "  make vault-status     Check Vault health"
	@echo "  make rotate-jwt       Rotate JWT signing secret"
	@echo "  make audit-verify     Verify audit log chain integrity"
	@echo "  make audit-tail       Show recent audit log entries"
	@echo "  make pii-test         Test PII scrubber"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-security-up   Start Vault + audit MinIO setup"
	@echo "  make docker-security-down Stop security containers"
	@echo ""
	@echo "Build:"
	@echo "  make clean            Remove all build artifacts"
	@echo "  make package          Build zip package for distribution"
	@echo ""

# ── Installation ───────────────────────────────────────────────────────────── #

install:
	@echo "Installing dependencies..."
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install -e ".[dev]"; \
	else \
		pip install -e ".[dev]" --break-system-packages; \
	fi

install-dev:
	@echo "Installing all extras..."
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install -e ".[all]"; \
	else \
		pip install -e ".[all]" --break-system-packages; \
	fi

install-spacy-model:
	@echo "Downloading spaCy model..."
	$(PYTHON) -m spacy download en_core_web_sm

# ── Testing ────────────────────────────────────────────────────────────────── #

test:
	@echo "Running all tests..."
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/ -v --tb=short

test-fast:
	@echo "Running unit tests (fast)..."
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/ -v --tb=short -q

test-integration:
	@echo "Running integration tests (requires running services)..."
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/integration/ -v --tb=short

test-security:
	@echo "Running Phase 12 security tests..."
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/ tests/integration/security/ -v --tb=short

coverage:
	@echo "Running tests with coverage (min $(COVERAGE_MIN)%)..."
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/ \
		--cov=src/aegis/security \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=$(COVERAGE_MIN) \
		-q
	@echo "HTML report: htmlcov/index.html"

# ── Code quality ───────────────────────────────────────────────────────────── #

lint:
	@echo "Running ruff linter..."
	PYTHONPATH=$(PYTHONPATH) $(RUFF) check src/ tests/

lint-fix:
	@echo "Running ruff linter with auto-fix..."
	PYTHONPATH=$(PYTHONPATH) $(RUFF) check --fix src/ tests/
	PYTHONPATH=$(PYTHONPATH) $(RUFF) format src/ tests/

type:
	@echo "Running mypy type checker..."
	PYTHONPATH=$(PYTHONPATH) $(MYPY) src/aegis/security/ --ignore-missing-imports

bandit:
	@echo "Running bandit security linter..."
	$(PYTHON) -m bandit -r src/ -f text --skip B101,B603,B607 -q

security-scan:
	@echo "Scanning repo for leaked secrets..."
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
from aegis.security.scanner import SecretScanner; \
from pathlib import Path; \
r = SecretScanner().scan_directory(Path('.')); \
print(r.to_dict()) if r.has_secrets else print('✔ No secrets detected')"

# ── Pre-commit ─────────────────────────────────────────────────────────────── #

pre-commit-install:
	@echo "Installing pre-commit hooks..."
	pre-commit install

pre-commit-run:
	@echo "Running all pre-commit hooks..."
	pre-commit run --all-files

# ── Security operations ────────────────────────────────────────────────────── #

doctor:
	@echo "Running AEGIS security doctor..."
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m aegis.security.cli security doctor

vault-init:
	@echo "Bootstrapping Vault..."
	bash bootstrap/security/01_vault_dev.sh
	@sleep 2
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
import asyncio; \
from aegis.security.vault.client import VaultClient; \
from aegis.security.vault.bootstrap import bootstrap_vault; \
async def run(): \
    async with VaultClient() as c: \
        results = await bootstrap_vault(c); \
    [print(f'  {r[\"status\"]:8}  {r[\"step\"]}') for r in results]; \
asyncio.run(run())"

vault-status:
	@echo "Checking Vault status..."
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
import asyncio; \
from aegis.security.vault.client import VaultClient; \
async def run(): \
    async with VaultClient() as c: \
        h = await c.health(); \
    [print(f'  {k}: {v}') for k,v in h.items()]; \
asyncio.run(run())"

rotate-jwt:
	@echo "Rotating JWT secret..."
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
import asyncio; \
from aegis.security.vault.client import VaultClient; \
from aegis.security.secrets.rotation import SecretRotationManager; \
async def run(): \
    async with VaultClient() as v: \
        mgr = SecretRotationManager(v); \
        rec = await mgr.rotate_jwt_secret(); \
        await mgr.complete_rotation('aegis/security/jwt'); \
        print(f'JWT secret rotated: v{rec.old_version} -> v{rec.new_version}'); \
asyncio.run(run())"

audit-verify:
	@echo "Verifying audit log integrity..."
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
import asyncio; \
from aegis.security.audit.logger import AuditLogger; \
async def run(): \
    logger = AuditLogger(); \
    valid, count, err = await logger.verify_integrity(); \
    status = '✔ INTACT' if valid else '✘ BROKEN'; \
    print(f'{status} — {count} entries verified'); \
    if err: print(f'Error: {err}'); \
asyncio.run(run())"

audit-tail:
	@echo "Recent audit log entries:"
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
from aegis.security.config import get_security_config; \
import json; from pathlib import Path; \
cfg = get_security_config(); \
p = Path(cfg.audit_log_path).expanduser(); \
lines = p.read_text().strip().split('\n') if p.exists() else []; \
[print(json.dumps(json.loads(l), indent=2)) for l in lines[-5:] if l.strip()]"

pii-test:
	@echo "Testing PII scrubber..."
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "\
from aegis.security.pii.scrubber import PIIScrubber; \
s = PIIScrubber(); s._spacy_available = False; \
samples = [ \
    'Contact alice@example.com or call +1-555-867-5309', \
    'Server at 192.168.1.100 with key AKIAIOSFODNN7EXAMPLE', \
    'Aadhaar: 1234 5678 9012, PAN: ABCDE1234F', \
]; \
[print(f'IN:  {t}\nOUT: {s.scrub_text(t)[0]}\n') for t in samples]"

# ── Docker ─────────────────────────────────────────────────────────────────── #

docker-security-up:
	@echo "Starting security containers (Vault + audit MinIO)..."
	docker compose -f docker-compose.yml -f docker-compose.security.yml up -d \
		aegis-vault aegis-vault-init aegis-minio-audit-init

docker-security-down:
	@echo "Stopping security containers..."
	docker compose -f docker-compose.security.yml down

# ── Build / Clean ──────────────────────────────────────────────────────────── #

clean: clean-pyc clean-coverage
	@echo "Cleaned."

clean-pyc:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

clean-coverage:
	rm -rf htmlcov/ .coverage coverage.xml

package:
	@echo "Building Phase 12 package..."
	$(MAKE) clean
	$(MAKE) test
	$(MAKE) lint
	cd .. && zip -r aegis-phase12-security.zip aegis-phase12 \
		--exclude "aegis-phase12/.coverage" \
		--exclude "aegis-phase12/.pytest_cache/*" \
		--exclude "aegis-phase12/**/__pycache__/*" \
		--exclude "aegis-phase12/**/*.pyc" \
		--exclude "aegis-phase12/htmlcov/*"
	@echo "Package: ../aegis-phase12-security.zip"

# ── Module test targets (spec requirement: make <module>.test) ─────────────── #

vault.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_mocked_external.py::TestVaultClientMocked -v

pii.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_phase12.py::TestPIIScrubber -v

audit.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_phase12.py::TestAuditLogger -v

rbac.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_phase12.py::TestRBACEnforcer -v

jwt.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_phase12.py::TestJWTManager -v

api.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_coverage_boost.py::TestSecurityAPI -v

crypto.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_phase12.py::TestCrypto -v

secrets.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_phase12.py::TestSecretsManager -v

scanner.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_scanner.py -v

rotation.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/unit/security/test_rotation.py -v

integration.test:
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/integration/security/ -v
