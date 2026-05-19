# Aurora — runtime container.
#
# This builds a minimal Aurora Studio image you can run anywhere
# Docker is available — local laptop, Fly.io, Railway, Render, your
# own VPS. The image is what powers the v1.2 "Aurora Cloud Phase 1"
# self-hosted distribution.
#
# Design choices:
#
#   * Python 3.12 slim — small base, modern Python, security-patched
#   * Multi-stage so the final image doesn't carry the build toolchain
#   * Non-root user (aurora:aurora) — defence in depth
#   * Two volumes: /var/aurora (persistent state — KB, run_dirs) and
#     /etc/aurora (config — LLM credentials, settings)
#   * Healthcheck hits /api/state so orchestrators (Fly, K8s) can
#     restart on hang
#   * No LLM bundled — Aurora's BYO-LLM model. Configure via env or
#     the Studio's settings UI to point at Ollama / Claude / OpenAI /
#     Gemini.

FROM python:3.12-slim AS builder

WORKDIR /build

# Build deps for any wheels that need compiling.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt \
    && pip install --prefix=/install --no-cache-dir mcp


# ----- Runtime stage --------------------------------------------------------
FROM python:3.12-slim

# Runtime deps (curl for healthcheck, ca-certs for outbound HTTPS to LLM APIs).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN groupadd --system aurora && useradd --system --gid aurora --create-home aurora

# Copy installed Python packages from builder.
COPY --from=builder /install /usr/local

# Copy Aurora source.
WORKDIR /opt/aurora
COPY --chown=aurora:aurora . /opt/aurora

# Volumes for persistent state. /var/aurora is the equivalent of
# ~/.aurora on a local install (KB, run history). /etc/aurora is for
# config the operator owns (LLM credentials, settings).
RUN mkdir -p /var/aurora /etc/aurora /opt/aurora/outputs/aurora_dataset_runs \
    && chown -R aurora:aurora /var/aurora /etc/aurora /opt/aurora/outputs

USER aurora

# Aurora respects these. AURORA_DATA_HOME overrides where ~/.aurora
# would normally live so the bind-mount actually does what users expect.
ENV AURORA_HOST=0.0.0.0 \
    AURORA_PORT=8000 \
    AURORA_NO_BROWSER=1 \
    AURORA_DATA_HOME=/var/aurora \
    AURORA_CONFIG_HOME=/etc/aurora \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent --show-error http://localhost:8000/api/state || exit 1

CMD ["python", "studio_api.py"]
