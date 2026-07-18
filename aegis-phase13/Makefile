# Makefile — AEGIS Pulse Phase 13: Testing & Quality
# Usage:
#   make test            — fast unit tests (no infra)
#   make test-fast       — unit tests, no coverage
#   make test-all        — unit + property + perf
#   make test-integration — integration tests (requires docker services)
#   make test-e2e        — end-to-end tests (requires full stack)
#   make test-perf       — benchmark tests
#   make test-mutation   — mutation testing (slow)
#   make lint            — ruff check + format check
#   make type            — mypy strict
#   make ci              — everything that runs in CI
#   make clean           — remove build artifacts

.PHONY: all test test-fast test-all test-unit test-integration test-e2e \
        test-perf test-mutation test-property lint type format ci clean \
        coverage-report install install-dev help

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PYTHON      := python
UV          := uv
PYTEST      := $(UV) run python -m pytest
RUFF        := $(UV) run ruff
MYPY        := $(UV) run mypy
COVERAGE    := $(UV) run coverage

# Test paths
UNIT_TESTS  := tests/unit
INTEG_TESTS := tests/integration
E2E_TESTS   := tests/e2e
PERF_TESTS  := tests/perf
PROP_TESTS  := tests/property

# Coverage config
COVERAGE_FLOOR := 78

# Colors
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
RESET  := \033[0m

# ---------------------------------------------------------------------------
# Default target
# ---------------------------------------------------------------------------

all: lint type test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(RESET) %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

install: ## Install all dependencies (main + test)
	$(UV) sync --all-packages --all-extras

install-dev: ## Install with dev/test extras
	$(UV) sync --all-packages --all-extras
	$(UV) run pre-commit install

# ---------------------------------------------------------------------------
# Test targets
# ---------------------------------------------------------------------------

test: test-unit ## Run unit tests (no infrastructure needed) — default

test-fast: ## Run unit tests without coverage (fastest)
	$(PYTEST) $(UNIT_TESTS) \
		-q \
		--no-header \
		--no-cov \
		-p no:hypothesis \
		-x \
		2>&1 | tail -20

test-unit: ## Run unit + property tests with coverage
	$(PYTEST) $(UNIT_TESTS) $(PROP_TESTS) \
		-q \
		--no-header \
		-p no:hypothesis \
		--cov=src/aegis \
		--cov-fail-under=$(COVERAGE_FLOOR) \
		--cov-report=term-missing:skip-covered \
		2>&1

test-property: ## Run Hypothesis property tests only
	HYPOTHESIS_PROFILE=ci \
	$(PYTEST) $(PROP_TESTS) \
		-v \
		--no-header \
		2>&1

test-all: test-unit test-property ## Run unit + property tests

test-integration: ## Run integration tests (requires AEGIS_TEST_INTEGRATION=1 + docker)
	AEGIS_TEST_INTEGRATION=1 \
	AEGIS_TEST_POSTGRES=1 \
	AEGIS_TEST_REDIS=1 \
	AEGIS_TEST_MINIO=1 \
	$(PYTEST) $(INTEG_TESTS) \
		-v \
		--no-header \
		--no-cov \
		--timeout=120 \
		2>&1

test-e2e: ## Run end-to-end tests (requires full docker-compose stack)
	AEGIS_TEST_INTEGRATION=1 \
	$(PYTEST) $(E2E_TESTS) \
		-v \
		--no-header \
		--no-cov \
		--timeout=300 \
		2>&1

test-perf: ## Run benchmark tests (shows regression vs baseline)
	$(PYTEST) $(PERF_TESTS) \
		--benchmark-only \
		--benchmark-sort=mean \
		--benchmark-columns=min,mean,max,stddev,rounds \
		--benchmark-json=.benchmarks/results.json \
		-v \
		--no-cov \
		2>&1

test-mutation: ## Run mutation testing (slow — target: ≥85% kill rate)
	$(PYTEST) $(UNIT_TESTS) \
		--no-cov \
		-q \
		-p no:hypothesis \
		-x && \
	$(PYTHON) tests/mutation/run_mutmut.py --ci

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint: ## Run ruff check + format check
	@echo "$(YELLOW)→ ruff check$(RESET)"
	$(RUFF) check src/ tests/ --show-source
	@echo "$(YELLOW)→ ruff format --check$(RESET)"
	$(RUFF) format --check src/ tests/
	@echo "$(GREEN)✓ lint passed$(RESET)"

lint-fix: ## Auto-fix ruff lint issues
	$(RUFF) check --fix src/ tests/
	$(RUFF) format src/ tests/

type: ## Run mypy strict type checking
	@echo "$(YELLOW)→ mypy --strict$(RESET)"
	$(MYPY) src/aegis/testing/ tests/unit/ tests/property/ \
		--ignore-missing-imports \
		--no-error-summary
	@echo "$(GREEN)✓ type check passed$(RESET)"

format: ## Format code with ruff
	$(RUFF) format src/ tests/

# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

coverage-report: ## Generate HTML coverage report
	$(COVERAGE) html
	@echo "$(GREEN)✓ Coverage report: htmlcov/index.html$(RESET)"

coverage-xml: ## Generate XML coverage report (for CI)
	$(COVERAGE) xml -o coverage.xml

# ---------------------------------------------------------------------------
# CI pipeline (mirrors GitHub Actions)
# ---------------------------------------------------------------------------

ci: ## Full CI pipeline: lint → type → test → coverage
	@echo "$(YELLOW)═══════════════════════════════════════$(RESET)"
	@echo "$(YELLOW) AEGIS Pulse Phase 13 — CI Pipeline$(RESET)"
	@echo "$(YELLOW)═══════════════════════════════════════$(RESET)"
	$(MAKE) lint
	$(MAKE) type
	$(MAKE) test-unit
	@echo "$(GREEN)═══════════════════════════════════════$(RESET)"
	@echo "$(GREEN) CI PASSED$(RESET)"
	@echo "$(GREEN)═══════════════════════════════════════$(RESET)"

ci-full: ci test-integration ## Full CI including integration tests

# ---------------------------------------------------------------------------
# Specific phase test targets (convenience wrappers)
# ---------------------------------------------------------------------------

test-phase0: ## Test Phase 0 (Scrape)
	$(PYTEST) tests/unit/scrape/ -v --no-cov -p no:hypothesis

test-phase2: ## Test Phase 2 (Agents)
	$(PYTEST) tests/unit/agents/ -v --no-cov -p no:hypothesis

test-phase3: ## Test Phase 3 (Predict) — unit + integration
	$(PYTEST) tests/unit/predict/ tests/integration/predict/ \
		-v --no-cov -p no:hypothesis

test-phase4: ## Test Phase 4 (Execute)
	$(PYTEST) tests/unit/execute/ -v --no-cov -p no:hypothesis

test-phase10: ## Test Phase 10 (Datalake)
	$(PYTEST) tests/unit/datalake/ -v --no-cov -p no:hypothesis

test-phase11: ## Test Phase 11 (LLM)
	$(PYTEST) tests/unit/llm/ -v --no-cov -p no:hypothesis

test-core: ## Test cross-cutting concerns
	$(PYTEST) tests/unit/core/ -v --no-cov -p no:hypothesis

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove build and test artifacts
	rm -rf htmlcov/ .coverage coverage.xml .benchmarks/
	rm -rf .mutmut-cache/ mutmut_cache/
	rm -rf .hypothesis/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@echo "$(GREEN)✓ cleaned$(RESET)"

# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

docs-errors: ## List all AEGIS error codes documented
	@find docs/errors/ -name "*.md" | sort | \
		while read f; do echo "  $$f"; done

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

health: ## Check that all test dependencies are importable
	$(PYTHON) -c "import pytest; print(f'pytest {pytest.__version__}')"
	$(PYTHON) -c "import hypothesis; print(f'hypothesis {hypothesis.__version__}')"
	$(PYTHON) -c "import pydantic; print(f'pydantic {pydantic.__version__}')"
	$(PYTHON) -c "import faker; print(f'faker {faker.VERSION}')"
	$(PYTHON) -c "import httpx; print(f'httpx {httpx.__version__}')"
	@echo "$(GREEN)✓ all test deps importable$(RESET)"
