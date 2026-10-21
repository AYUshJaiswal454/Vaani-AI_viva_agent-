# Vaani: one image for the web app (live vivas over WebSocket) and the background worker.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data MODELS_DIR=/app/models
# Speech models baked into the image (about 200 MB for base.en). Set DOWNLOAD_MODELS=0 for a small image that uses
# a hosted recogniser (GROQ_API_KEY / OPENAI_API_KEY / SARVAM_API_KEY) or the browser's.
ARG DOWNLOAD_MODELS=1
ARG WHISPER_MODEL=base.en

# ffmpeg: Opus encoding of recordings, audio decoding, and the offline flite voice (Debian's build includes libflite)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY scripts ./scripts
RUN if [ "$DOWNLOAD_MODELS" = "1" ]; then python scripts/download_models.py --whisper "$WHISPER_MODEL" --dir /app/models; fi
COPY app ./app
COPY samples ./samples
COPY tests ./tests
COPY loadtest ./loadtest

RUN useradd --create-home --uid 1000 vaani && mkdir -p /data /app/models && chown -R vaani /data /app
USER vaani
ENV PORT=8000 WHISPER_MODEL=${WHISPER_MODEL}
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4)"
# WEB_WORKERS: web processes (one per CPU core suits hosted speech recognition; with local Whisper keep 1 and let
# STT_CONCURRENCY use the cores). Live vivas survive moving between processes because state is saved every answer.
ENV WEB_WORKERS=1
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --ws wsproto --workers ${WEB_WORKERS} --proxy-headers --forwarded-allow-ips='*'"]
