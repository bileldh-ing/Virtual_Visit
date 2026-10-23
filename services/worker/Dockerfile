# syntax=docker/dockerfile:1.7
# RoomScape reconstruction worker: RQ + COLMAP (CPU build from Debian) + the free depth-room engine
# (onnxruntime + Depth Anything V2 Small, Apache-2.0, pinned revision and sha256). Build context: repository root.
# For GPU dense reconstruction, build FROM an NVIDIA CUDA image with a CUDA-enabled COLMAP instead.
FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 QT_QPA_PLATFORM=offscreen
RUN apt-get update && apt-get install -y --no-install-recommends colmap libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# Depth Anything V2 Small (ONNX). Baked into the image so the fallback never depends on Hugging Face at run time.
ARG DEPTH_MODEL_REVISION=4472b7362082ad9968fee890ca0f1e5aca36b93d
ARG DEPTH_MODEL_SHA256=afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c
RUN mkdir -p /opt/models/depth-anything-v2-small \
    && curl -fsSL --retry 5 -o /opt/models/depth-anything-v2-small/model.onnx \
       "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/${DEPTH_MODEL_REVISION}/onnx/model.onnx" \
    && echo "${DEPTH_MODEL_SHA256}  /opt/models/depth-anything-v2-small/model.onnx" | sha256sum -c -

COPY services/reconstruction /app/services/reconstruction
COPY services/api /app/services/api
COPY services/worker /app/services/worker
RUN pip install "/app/services/reconstruction[depth]" /app/services/api /app/services/worker \
    && python -c "import onnxruntime; print('onnxruntime', onnxruntime.__version__)"
RUN useradd --system --uid 10001 --home /app roomscape && mkdir -p /data/jobs && chown -R roomscape /data
USER roomscape
ENV ROOMSCAPE_WORKER_WORKSPACE=/data/jobs \
    ROOMSCAPE_DEPTH_MODEL_PATH=/opt/models/depth-anything-v2-small/model.onnx \
    ROOMSCAPE_DEPTH_MODEL_DOWNLOAD=false
CMD ["roomscape-worker"]
