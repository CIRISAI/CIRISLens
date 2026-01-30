# CIRISLens API Docker Image
# Multi-stage build with Rust extension support

# ============================================
# Stage 1: Rust builder for cirislens-core
# ============================================
FROM python:3.11-slim AS rust-builder

WORKDIR /build

# Install Rust toolchain and build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust via rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

# Install maturin for building PyO3 extensions
RUN pip install --no-cache-dir maturin>=1.4

# Copy Rust crate source
COPY cirislens-core/ /build/cirislens-core/

# Build the Rust extension wheel
WORKDIR /build/cirislens-core
RUN maturin build --release --strip --out /build/wheels

# ============================================
# Stage 2: Python builder
# ============================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files (context is repo root)
COPY api/requirements.txt api/pyproject.toml* ./

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m spacy download en_core_web_sm

# Copy and install the Rust extension wheel
COPY --from=rust-builder /build/wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl

# ============================================
# Stage 3: Production stage
# ============================================
FROM python:3.11-slim AS production

LABEL org.opencontainers.image.title="CIRISLens API"
LABEL org.opencontainers.image.description="Observability layer for the CIRIS ecosystem"
LABEL org.opencontainers.image.vendor="CIRIS"
LABEL org.opencontainers.image.source="https://github.com/CIRISAI/CIRISLens"

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Default settings (can be overridden)
    PORT=8000 \
    WORKERS=4

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code (context is repo root)
COPY api/ .

# Copy SQL migrations
COPY sql/ /app/sql/

# Copy schema definitions
COPY schemas/ /app/schemas/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 cirislens && \
    chown -R cirislens:cirislens /app

USER cirislens

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Run with uvicorn (production settings)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers ${WORKERS} --access-log --proxy-headers"]

# ============================================
# Stage 4: Development stage (optional)
# ============================================
FROM production AS development

USER root

# Install dev dependencies
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov httpx ruff mypy

USER cirislens

# Override CMD for development (with hot reload)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --reload"]
