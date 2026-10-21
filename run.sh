#!/usr/bin/env bash
# One-command setup and start (Linux/macOS): ./run.sh
# Creates a virtualenv, installs dependencies, downloads the speech models (once, ~200 MB) and starts Vaani.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
[ -d .venv ] || { echo "Creating virtualenv…"; $PY -m venv .venv; }
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt
[ -f .env ] || cp .env.example .env
python scripts/download_models.py --whisper "${WHISPER_MODEL:-base.en}" || echo "Note: model download failed; set STT_ENGINE=browser or add GROQ_API_KEY."
command -v ffmpeg >/dev/null || echo "Note: ffmpeg not found (apt install ffmpeg / brew install ffmpeg): recordings are kept as WAV and the flite voice is off."
echo
echo "Vaani is starting at http://localhost:${PORT:-8000}"
echo "  Faculty: faculty@vaani.local / faculty123      Students: open the page and join with code DBMS26 (any roll number)"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --ws wsproto
