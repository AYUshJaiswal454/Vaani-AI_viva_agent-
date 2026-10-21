# One-command setup and start (Windows PowerShell):  powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { Write-Host "Creating virtualenv..."; python -m venv .venv }
. .\.venv\Scripts\Activate.ps1
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$model = if ($env:WHISPER_MODEL) { $env:WHISPER_MODEL } else { "base.en" }
try { python scripts\download_models.py --whisper $model } catch { Write-Host "Note: model download failed; set STT_ENGINE=browser or add GROQ_API_KEY." }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Write-Host "Note: ffmpeg not found (winget install ffmpeg): recordings are kept as WAV and the flite voice is off." }
Write-Host ""
Write-Host "Vaani is starting at http://localhost:8000"
Write-Host "  Faculty: faculty@vaani.local / faculty123      Students: join with code DBMS26 (any roll number)"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws wsproto
