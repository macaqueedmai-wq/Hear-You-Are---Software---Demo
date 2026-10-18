# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HYA_DATA_DIR=/var/lib/hear-you-are \
    HYA_ASR_PROVIDER=sensevoice \
    HYA_SENSEVOICE_MODEL_PATH=/var/cache/hear-you-are/sensevoice/model.int8.onnx \
    HYA_SENSEVOICE_TOKENS_PATH=/var/cache/hear-you-are/sensevoice/tokens.txt \
    HYA_SILERO_VAD_MODEL_PATH=/var/cache/hear-you-are/sensevoice/silero_vad.onnx \
    HYA_DIARIZATION_SEGMENTATION_PATH=/var/cache/hear-you-are/diarization/segmentation.onnx \
    HYA_DIARIZATION_EMBEDDING_PATH=/var/cache/hear-you-are/diarization/embedding.onnx \
    HYA_MEMORY_MODE=deepseek \
    HYA_ALLOW_EXTERNAL_LLM=true \
    HYA_DEPLOYMENT_MODE=server
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/hear-you-are
COPY requirements.txt requirements-sensevoice.txt ./
RUN python -m pip install --no-cache-dir -r requirements-sensevoice.txt
RUN groupadd --gid 10001 hya \
    && useradd --uid 10001 --gid hya --create-home --shell /usr/sbin/nologin hya \
    && mkdir -p /var/lib/hear-you-are /var/cache/hear-you-are \
    && chown -R hya:hya /var/lib/hear-you-are /var/cache/hear-you-are
COPY backend ./backend
COPY scripts/download_qwen_models.py scripts/download_sensevoice_models.py ./scripts/
# Host extraction may leave root-only directories; the nonroot runtime must import the code.
RUN chmod -R a+rX ./backend ./scripts
COPY --from=frontend /build/web/dist ./web/dist
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).read()"]
# Background task coordination and SQLite mutations assume one app process.
CMD ["python", "-m", "uvicorn", "backend.hya.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
