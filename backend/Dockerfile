FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 lawmind

FROM base AS dependencies

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

FROM base AS production

COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin
COPY --chown=lawmind:lawmind . .

# Chroma writes its default embedding model under $HOME/.cache/chroma. Pre-create
# it owned by the runtime user so a named volume mounted there stays writable.
RUN mkdir -p /app/data/chroma /app/logs /home/lawmind/.cache/chroma \
    && chown -R lawmind:lawmind /app/data /app/logs /home/lawmind/.cache

USER lawmind

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM dependencies AS development

COPY --chown=lawmind:lawmind . .

RUN mkdir -p /app/data/chroma /app/logs /app/tests \
    && chmod -R 777 /app/data /app/logs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
