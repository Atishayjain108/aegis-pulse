# =============================================================================
# Makefile — Phase 11 developer UX
# =============================================================================
#
# Usage:
#   make install        Install all dependencies
#   make test           Run unit tests
#   make test-fast      Run tests skipping slow/embed tests
#   make lint           Ruff lint + format check
#   make type           mypy strict type check
#   make health         Check all provider health
#   make pull-models    Pull required Ollama models
#   make eval           Run nightly golden-answer eval
#   make clean          Remove build artifacts
# =============================================================================

.PHONY: all install test test-fast lint type format health pull-models eval \
        clean docker-up docker-down

PYTHON := python3.12
UV     := uv
SRC    := src/aegis/llm
TESTS  := tests/unit/llm

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

install:
	$(UV) sync --all-extras
	@echo "✓ Dependencies installed"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test:
	$(UV) run pytest $(TESTS) \
		--cov=$(SRC) \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		-p no:hypothesis \
		-v
	@echo "✓ Tests complete"

test-fast:
	$(UV) run pytest $(TESTS) \
		-p no:hypothesis \
		-k "not embed and not pull" \
		-q
	@echo "✓ Fast tests complete"

test-all:
	$(UV) run pytest tests/ \
		--cov=$(SRC) \
		--cov-fail-under=82 \
		-p no:hypothesis \
		-q

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint:
	$(UV) run ruff check $(SRC) $(TESTS)
	$(UV) run ruff format --check $(SRC) $(TESTS)
	@echo "✓ Lint passed"

format:
	$(UV) run ruff format $(SRC) $(TESTS)
	$(UV) run ruff check --fix $(SRC) $(TESTS)
	@echo "✓ Format applied"

type:
	$(UV) run mypy $(SRC) --ignore-missing-imports
	@echo "✓ Type check passed"

# ---------------------------------------------------------------------------
# LLM operations
# ---------------------------------------------------------------------------

health:
	$(UV) run aegis llm health

pull-models:
	bash bootstrap/wsl/06_ollama_models.sh

pull-models-minimal:
	bash bootstrap/wsl/06_ollama_models.sh --minimal

pull-models-cpu:
	bash bootstrap/wsl/06_ollama_models.sh --cpu-only

eval:
	$(UV) run aegis llm eval --fail-fast

models:
	$(UV) run aegis llm models

cost:
	$(UV) run aegis llm cost

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-up:
	docker compose -f docker-compose.phase11.yml up -d
	@echo "✓ Phase 11 services started"

docker-down:
	docker compose -f docker-compose.phase11.yml down
	@echo "✓ Phase 11 services stopped"

docker-logs:
	docker compose -f docker-compose.phase11.yml logs -f

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean:
	rm -rf .pytest_cache htmlcov .coverage dist build __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "✓ Clean complete"

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

help:
	@echo ""
	@echo "AEGIS Phase 11 — Local LLM Orchestration"
	@echo "==========================================="
	@echo "  make install       Install dependencies"
	@echo "  make test          Full test suite with coverage"
	@echo "  make test-fast     Quick tests (no embed/pull)"
	@echo "  make lint          Ruff lint check"
	@echo "  make format        Auto-format code"
	@echo "  make type          mypy strict type check"
	@echo "  make health        Provider health check"
	@echo "  make pull-models   Pull Ollama model zoo"
	@echo "  make eval          Nightly golden-answer eval"
	@echo "  make docker-up     Start Phase 11 Docker services"
	@echo "  make docker-down   Stop Phase 11 Docker services"
	@echo "  make clean         Remove build artifacts"
	@echo ""
