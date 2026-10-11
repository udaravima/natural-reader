# Natural Reader backend (FastAPI + Kokoro TTS). Python is pinned here so host
# OS upgrades can't move the interpreter under the app.
# Build:  ./startup.sh rebuild   (or: podman build -f Containerfile -t natural-reader-backend .)
FROM docker.io/library/python:3.13-slim-trixie

ARG WITH_DOCLING=false
ARG APP_UID=1000
ARG MODEL_BASE_URL=https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/app \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# onnxruntime needs libgomp; Docling's OpenCV needs libGL + glib.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && if [ "$WITH_DOCLING" = "true" ]; then apt-get install -y --no-install-recommends libgl1 libglib2.0-0; fi \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-docling.txt ./
RUN pip install -r requirements.txt
# CPU-only torch keeps the Docling variant gigabytes smaller than the CUDA default.
RUN if [ "$WITH_DOCLING" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-docling.txt; \
    fi

# Kokoro voice model, in its own cached layer. server/model.py opens these
# relative to the working directory (/app).
RUN python -c "import urllib.request as u, os; \
[u.urlretrieve('$MODEL_BASE_URL/' + n, n) for n in ('kokoro-v1.0.onnx', 'voices-v1.0.bin')]; \
assert all(os.path.getsize(n) > 1_000_000 for n in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'))"

RUN useradd --uid "$APP_UID" --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app/data/pdfs /app/logs /app/.cache /app/config \
 && chown app:app /app /app/data /app/data/pdfs /app/logs /app/.cache /app/config

COPY run.py ./
COPY server/ server/

USER app
CMD ["python", "run.py"]
