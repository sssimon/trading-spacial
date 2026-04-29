# trading-spacial backend — multi-stage build, non-root runtime user.
#
# Stage 1 (builder): install Python deps into a venv. Keeps build tools out
# of the final image.
# Stage 2 (runtime): minimal slim image, copies the venv + source, runs as
# uid 1000 ('trading'). Smaller image, no root in the running container.

# ──────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for compiling wheels (numpy, bcrypt, lxml etc).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libxml2-dev \
        libxslt-dev \
        libffi-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create a venv we'll copy into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app"

# Runtime libs only (no compilers): lxml needs libxml2 and libxslt at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        ca-certificates \
        curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. uid 1000 matches the typical host user; a host-mounted
# volume with files owned by uid 1000 is read/writable without chown.
RUN useradd -m -u 1000 trading

# Copy the venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy source. Ownership goes to the trading user so the process can write
# to /app/data and /app/logs without permission errors.
COPY --chown=trading:trading . /app

# Pre-create directories the app writes into. Their ownership is set by the
# COPY above, but if the host volume mount overlays /app/data we still need
# the dir to exist with the right uid.
RUN mkdir -p /app/data /app/logs \
 && chown -R trading:trading /app/data /app/logs

USER trading

EXPOSE 8000

# Lightweight healthcheck — the bare /health endpoint is whitelisted in the
# auth middleware specifically for monitoring/docker.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "btc_api:app", "--host", "0.0.0.0", "--port", "8000"]
