# syntax=docker/dockerfile:1
# REGISTRY lets you pull official images through a mirror (e.g. mirror.gcr.io/library/)
ARG REGISTRY=

# ---- builder: resolve and install dependencies into an isolated venv ----
FROM ${REGISTRY}python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- runtime: slim base + the venv + app code, running as non-root ----
FROM ${REGISTRY}python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    DATABASE_URL=sqlite:////data/app.db \
    PORT=8000
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=app:app app ./app
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1
# Single worker on purpose: SQLite writes, per-session locks and the in-process
# key-health state all assume one process. Scale vertically, or move state out.
CMD ["sh", "-c", "exec uvicorn app.asgi:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
