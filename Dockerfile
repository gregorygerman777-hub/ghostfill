# ghostfill: paper trading only. The image contains no keys and no RPC URL;
# RPC_URL is passed at runtime.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JOURNAL_DB_PATH=/data/journal.sqlite3 \
    CACHE_DIR=/data/cache \
    GHOSTFILL_CONFIG=/app/config/config.yaml

RUN groupadd --system --gid 10001 ghostfill \
 && useradd --system --uid 10001 --gid ghostfill --home-dir /app --shell /usr/sbin/nologin ghostfill \
 && mkdir -p /data && chown ghostfill:ghostfill /data

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .
COPY config ./config

USER ghostfill
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["ghostfill"]
CMD ["run", "--host", "0.0.0.0", "--port", "8000"]
