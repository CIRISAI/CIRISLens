# CIRISLens API Docker Image
# Multi-stage build with Rust extension support

# ============================================
# Stage 1: Rust builder for cirislens-core
# ============================================
# ciris-persist used to be built here too (clone + maturin from source).
# As of v0.1.13 it's published to PyPI with abi3 wheels for all three
# of our targets — manylinux_2_34_x86_64, manylinux_2_34_aarch64,
# macosx_11_0_arm64 — so the lens just `pip install`s it in stage 2.
# That collapses cold-build wall from ~2h to ~10 min on the persist
# side; only cirislens-core's compile remains here.
FROM python:3.11-slim AS rust-builder

WORKDIR /build

# Install Rust toolchain and build dependencies. `patchelf` is needed
# by maturin to fix up the wheel's RPATH on Linux when the wheel
# imports a dynamic library (in our case `libonnxruntime.so` via the
# `ort` crate's `load-dynamic` linkage).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    patchelf \
    && rm -rf /var/lib/apt/lists/*

# Install Rust via rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:$PATH"

# Install maturin for building PyO3 extensions
RUN pip install --no-cache-dir maturin>=1.4

# ─── cirislens-core (NER scrubber + signature verifier) ──────────────
# Copy Rust crate source
COPY cirislens-core/ /build/cirislens-core/

# Build the Rust extension wheel with the ort backend feature so the
# fast-path INT8 NER scrubber is available at runtime. The candle path
# also stays compiled in (the `ner-ort` feature implies `ner`), so we
# can fall back via env var if the ort load fails.
WORKDIR /build/cirislens-core
RUN maturin build --release --strip --features ner-ort --out /build/wheels

# ============================================
# Stage 1b: NER model export (ONNX + INT8 quantized DistilBERT-multilingual)
#
# Bake the model bundle into the image so the runtime doesn't have to
# fetch from HF Hub at first call (slow + needs network at startup).
# Bundle is ~130 MB INT8.
# ============================================
FROM python:3.11-slim AS model-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    'optimum[onnxruntime]>=2.1.0' \
    'transformers>=4.40' \
    'onnx>=1.17' \
    accelerate

COPY scripts/export_ner_onnx.py /build/scripts/export_ner_onnx.py
RUN python /build/scripts/export_ner_onnx.py \
    --model Davlan/distilbert-base-multilingual-cased-ner-hrl \
    --out /build/models/distilbert_int8

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
    pip install --no-cache-dir -r requirements.txt

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
    WORKERS=4 \
    # Scrubber v2 NER fast path: ort + INT8 + DistilBERT-multilingual.
    # Bundle baked into the image at /app/models/distilbert_int8.
    # Operators can override CIRISLENS_NER_BACKBONE=candle to fall back
    # to the Rust-native FP32 path if needed.
    CIRISLENS_NER_BACKBONE=ort \
    CIRISLENS_NER_ORT_DIR=/app/models/distilbert_int8 \
    ORT_DYLIB_PATH=/opt/venv/lib/python3.11/site-packages/onnxruntime/capi/libonnxruntime.so.1.25.0

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

# Copy the pre-exported NER model bundle (ONNX + INT8 quantized
# DistilBERT-multilingual + tokenizer + config).
COPY --from=model-builder /build/models /app/models

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
