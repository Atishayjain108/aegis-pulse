# syntax=docker/dockerfile:1.7
# AEGIS Pulse — Phase 5 (Adversarial Hardening) container.
#
# Image is intentionally minimal — only `aegis-harden` CLI is exposed.
# The CLI runs `doctor`, `playbooks validate`, and other utility checks
# in CI/CD. There is no long-running server in this image — Phase 5
# defenses are libraries consumed by Phase 1 and Phase 3.

ARG PYTHON_VERSION=3.12-slim-bookworm

# ---------------------------------------------------------------------------
# Stage 1: builder — install deps into a virtualenv we can copy over
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System build deps. Keep this list minimal.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install into a venv so we can copy it cleanly into the runtime image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
COPY src ./src
COPY playbooks ./playbooks

# Optional: include `metrics` extra for prometheus_client.
RUN pip install ".[metrics]"

# ---------------------------------------------------------------------------
# Stage 2: runtime — minimal, non-root, tini
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AEGIS_HARDEN_PLAYBOOKS_DIR=/app/playbooks \
    PATH="/opt/venv/bin:${PATH}"

# tini for proper PID 1 signal handling.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tini \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN groupadd --system --gid 10005 aegis && \
    useradd --system --uid 10005 --gid aegis --no-create-home --shell /usr/sbin/nologin aegis

WORKDIR /app

# Copy the prepared venv and the playbooks shipped with the image.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/playbooks /app/playbooks
COPY --from=builder /build/src /app/src

# Health-check uses the built-in `doctor` command.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD aegis-harden doctor || exit 1

USER aegis

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["aegis-harden", "doctor"]
