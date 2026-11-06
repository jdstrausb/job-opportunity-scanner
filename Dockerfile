# Dockerfile for Job Opportunity Scanner
# Builds a production-ready container with Python 3.13, non-root execution, and SQLite persistence

# Build arguments for traceability
ARG PYTHON_VERSION=3.13
ARG APP_VERSION=1.0.0
ARG BUILD_DATE

# Stage 1: build the static SvelteKit SPA (skipped cleanly until frontend/ has a package.json)
FROM node:22-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/ ./
RUN if [ -f package.json ]; then npm ci && npm run build; else mkdir -p build; fi

# Stage 2: Python runtime (base image)
FROM python:${PYTHON_VERSION}-slim AS base

# Labels for metadata
LABEL org.opencontainers.image.title="Job Opportunity Scanner"
LABEL org.opencontainers.image.description="Automated service that monitors ATS APIs for job postings"
LABEL org.opencontainers.image.version="${APP_VERSION}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        curl \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user and group
RUN groupadd -r scanner && \
    useradd -r -g scanner -d /home/scanner -m -s /bin/bash scanner

# Copy dependency manifests first for better layer caching
COPY pyproject.toml uv.lock ./

# Upgrade pip and install project dependencies
RUN pip install --no-cache-dir --disable-pip-version-check --upgrade pip && \
    pip install --no-cache-dir --disable-pip-version-check .

# Copy application source
COPY app/ ./app/
COPY config.example.yaml ./config/example.config.yaml
COPY verify_config.py ./
COPY README.md ./

# Compiled web UI, served by the FastAPI app from ./frontend/build
COPY --from=frontend-builder /app/frontend/build ./frontend/build

# Create data directory and cache directory with proper permissions
RUN mkdir -p /app/data /app/.cache && \
    chown -R scanner:scanner /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    UI_HOST=0.0.0.0 \
    UI_PORT=8000 \
    PATH="/home/scanner/.local/bin:${PATH}"

# Switch to non-root user
USER scanner

# Health check (optional - monitors process health)
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f job-scanner || exit 1

# Web UI and REST API (disable with --no-ui, which listens on no port)
EXPOSE 8000

# Set entrypoint to the console script
ENTRYPOINT ["job-scanner"]

# Default command: daemon with web UI (override with --no-ui, --manual-run or other flags)
CMD []
