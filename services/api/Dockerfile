# syntax=docker/dockerfile:1.7
# RoomScape API (FastAPI). Build context: repository root.
FROM python:3.12-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/reconstruction /app/services/reconstruction
COPY services/api /app/services/api
RUN pip install /app/services/reconstruction /app/services/api
RUN useradd --system --uid 10001 --home /app roomscape && mkdir -p /data && chown roomscape /data
USER roomscape
ENV ROOMSCAPE_STORAGE_LOCAL_ROOT=/data/storage
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["sh", "-c", "python -m roomscape_api.migrate && exec uvicorn roomscape_api.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
