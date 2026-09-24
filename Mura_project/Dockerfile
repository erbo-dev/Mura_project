# ==============================================================================
# Stage 1: Build virtual environment with all production dependencies
# ==============================================================================
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip hatchling

COPY pyproject.toml README.md ./
COPY src ./src
COPY apps ./apps
COPY services ./services
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install .

# ==============================================================================
# Stage 2: Minimal production runtime for FastAPI Core API
# ==============================================================================
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

WORKDIR /app

# Copy compiled virtualenv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy source tree and Alembic migrations
COPY src ./src
COPY apps ./apps
COPY services ./services
COPY migrations ./migrations
COPY alembic.ini pyproject.toml README.md ./

# Create non-root system user and storage directories
RUN groupadd -g 10001 mura && \
    useradd -u 10001 -g mura -d /app -s /bin/bash mura && \
    mkdir -p /app/data/audio /app/.mura/audio && \
    mkdir -p /app/data/audio /app/.mura/audio /app/data/books /app/.mura/books && \
    chown -R mura:mura /app

USER mura

EXPOSE 8000

# Native healthcheck using Python's standard library
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", 8000)}/health')" || exit 1

# Trust forwarded headers only from the configured reverse proxy CIDRs/IPs.
# Deployment must inject FORWARDED_ALLOW_IPS for the actual proxy network; loopback is the safe fallback.
# CoreSettings rejects an empty or wildcard FORWARDED_ALLOW_IPS in production-like environments.
CMD ["sh", "-c", "exec uvicorn apps.api.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
