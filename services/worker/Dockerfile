# syntax=docker/dockerfile:1.7
# RoomScape reconstruction worker: RQ + COLMAP (CPU build from Debian). Build context: repository root.
# For GPU dense reconstruction, build FROM an NVIDIA CUDA image with a CUDA-enabled COLMAP instead.
FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 QT_QPA_PLATFORM=offscreen
RUN apt-get update && apt-get install -y --no-install-recommends colmap libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/reconstruction /app/services/reconstruction
COPY services/api /app/services/api
COPY services/worker /app/services/worker
RUN pip install /app/services/reconstruction /app/services/api /app/services/worker
RUN useradd --system --uid 10001 --home /app roomscape && mkdir -p /data/jobs && chown -R roomscape /data
USER roomscape
ENV ROOMSCAPE_WORKER_WORKSPACE=/data/jobs
CMD ["roomscape-worker"]
