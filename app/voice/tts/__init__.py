"""Text-to-speech engines with a shared phrase cache.

    browser   the student's browser speaks (Web Speech API); zero server cost, voice depends on the device
    flite     offline, always available with ffmpeg; robotic but dependable (also used for replays and test audio)
    piper     offline neural voices (pip install piper-tts; voice files in models/piper)
    openai    OpenAI speech (gpt-4o-mini-tts), natural, with a calm-examiner instruction
    sarvam    Sarvam AI Bulbul, Indian voices for English and Hindi
    stub      load tests: a short silent clip sized like real speech

Every approved bank question, follow-up, hint and system line is synthesised once when a viva is opened and cached
by (engine, voice, text). During the viva those clips play instantly; only dynamic text (the greeting with the
student's name, LLM-phrased follow-ups) is synthesised live. One cached clip serves all 350 students.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ...config import DATA_FILES, get_settings
from ...db import get_db, now_iso
from ...llm.providers import record_usage
from ...net.http import client
from ..audio import SR, decode_to_pcm, encode_pcm, wav_bytes

log = logging.getLogger("vaani.tts")
PRICING = json.loads((DATA_FILES / "pricing.json").read_text(encoding="utf-8"))
ENGINES = ("browser", "flite", "piper", "openai", "sarvam", "stub")
SERVER_ENGINES = ("flite", "piper", "openai", "sarvam", "stub")
EXAMINER_STYLE = "Speak like a calm, friendly university examiner: clear, unhurried, neutral Indian-English friendly pacing."
_locks: dict[str, asyncio.Lock] = {}


def voice_for(engine: str) -> str:
    s = get_settings()
    return {"flite": s.flite_voice, "piper": s.piper_voice, "openai": s.openai_tts_voice, "sarvam": s.sarvam_tts_voice,
            "stub": "silence"}.get(engine, "")


def cache_key(engine: str, voice: str, text: str) -> str:
    return hashlib.sha256(f"{engine}|{voice}|{text.strip()}".encode()).hexdigest()[:32]


def lookup(engine: str, text: str) -> dict | None:
    return get_db().fetchone("SELECT * FROM tts_cache WHERE key = ?", (cache_key(engine, voice_for(engine), text),))


async def get_clip(text: str, engine: str | None = None, session_id: int | None = None) -> dict | None:
    """Return the cached clip for `text` (synthesising it on a miss). None for the browser engine."""
    engine = engine or get_settings().tts_engine
    if engine not in SERVER_ENGINES or not text.strip():
        return None
    voice = voice_for(engine)
    key = cache_key(engine, voice, text)
    row = get_db().fetchone("SELECT * FROM tts_cache WHERE key = ?", (key,))
    if row and (get_settings().tts_dir / row["path"]).exists():
        return row
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        row = get_db().fetchone("SELECT * FROM tts_cache WHERE key = ?", (key,))
        if row and (get_settings().tts_dir / row["path"]).exists():
            return row
        t0 = time.perf_counter()
        data, mime, dur = await synthesize(text, engine, voice)
        ext = {"audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/wav": "wav"}.get(mime, "bin")
        rel = f"{engine}/{key[:2]}/{key}.{ext}"
        path = get_settings().tts_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        get_db().execute(
            "INSERT INTO tts_cache (key, engine, voice, text, path, mime, duration_s, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET path = excluded.path, mime = excluded.mime, duration_s = excluded.duration_s",
            (key, engine, voice, text.strip(), rel, mime, dur, now_iso()))
        cost = 0.0
        if engine == "openai":
            cost = PRICING["tts_per_minute_usd"].get(get_settings().openai_tts_model, 0.0) * dur / 60
        elif engine == "sarvam":
            cost = PRICING["sarvam_inr"]["tts_per_10k_chars"] * len(text) / 10000 / PRICING["usd_to_inr"]
        record_usage(session_id, "tts", engine if engine in ("openai", "sarvam") else "local", engine, {},
                     int((time.perf_counter() - t0) * 1000), characters=len(text), cost=cost)
        return get_db().fetchone("SELECT * FROM tts_cache WHERE key = ?", (key,))


async def synthesize(text: str, engine: str, voice: str) -> tuple[bytes, str, float]:
    if engine == "flite":
        pcm = await asyncio.get_running_loop().run_in_executor(None, _flite_pcm, text, voice)
    elif engine == "piper":
        pcm = await asyncio.get_running_loop().run_in_executor(None, _piper_pcm, text, voice)
    elif engine == "openai":
        pcm = await _openai_pcm(text, voice)
    elif engine == "sarvam":
        pcm = await _sarvam_pcm(text, voice)
    elif engine == "stub":
        pcm = b"\x00\x00" * int(SR * max(0.6, len(text) / 15.0))
        return wav_bytes(pcm), "audio/wav", round(len(pcm) / 2 / SR, 2)      # no encoding: load tests stay cheap
    else:
        raise ValueError(f"{engine} is not a server-side TTS engine")
    dur = len(pcm) / 2 / SR
    data, mime = await asyncio.get_running_loop().run_in_executor(None, encode_pcm, pcm, "mp3")
    return data, mime, round(dur, 2)


def _flite_pcm(text: str, voice: str) -> bytes:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg (with libflite) is needed for the offline voice")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text.replace("\n", " "))
        tf = fh.name
    try:
        src = f"flite=textfile='{tf}':voice={voice or 'slt'}"
        p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", src, "-ar", str(SR), "-ac", "1",
                            "-f", "s16le", "pipe:1"], capture_output=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="ignore")[-200:])
        return p.stdout
    finally:
        Path(tf).unlink(missing_ok=True)


def _piper_pcm(text: str, voice: str) -> bytes:
    """Piper CLI (pip install piper-tts). Voice files: models/piper/<voice>.onnx(+.json)."""
    exe = shutil.which("piper")
    model = get_settings().models_dir / "piper" / f"{voice}.onnx"
    if not exe or not model.exists():
        raise RuntimeError("Piper is not installed or the voice file is missing (see docs/SPEECH.md)")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.wav"
        p = subprocess.run([exe, "--model", str(model), "--output_file", str(out)], input=text.encode(), capture_output=True,
                           timeout=60)
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(p.stderr.decode(errors="ignore")[-200:])
        return decode_to_pcm(out.read_bytes())


async def _openai_pcm(text: str, voice: str) -> bytes:
    s = get_settings()
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    body = {"model": s.openai_tts_model, "voice": voice, "input": text, "response_format": "wav", "instructions": EXAMINER_STYLE}
    resp = await client().post(s.openai_base_url.rstrip("/") + "/audio/speech", json=body,
                               headers={"authorization": f"Bearer {s.openai_api_key}"}, timeout=30)
    if resp.status_code == 400 and "instructions" in resp.text:
        body.pop("instructions")
        resp = await client().post(s.openai_base_url.rstrip("/") + "/audio/speech", json=body,
                                   headers={"authorization": f"Bearer {s.openai_api_key}"}, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
    return decode_to_pcm(resp.content)


async def _sarvam_pcm(text: str, voice: str) -> bytes:
    s = get_settings()
    if not s.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is not set")
    body = {"text": text[:1500], "target_language_code": "en-IN", "speaker": voice, "model": s.sarvam_tts_model,
            "speech_sample_rate": 16000, "enable_preprocessing": True}
    resp = await client().post("https://api.sarvam.ai/text-to-speech", json=body,
                               headers={"api-subscription-key": s.sarvam_api_key}, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    clips = data.get("audios") or data.get("audio") or []
    if isinstance(clips, str):
        clips = [clips]
    return b"".join(decode_to_pcm(base64.b64decode(c)) for c in clips)


def clip_url(row: dict | None) -> str | None:
    return f"/api/tts/{row['key']}" if row else None


def replay_engine() -> str:
    """Server voice used to rebuild the examiner's side of a recording when the live voice was the browser's."""
    s = get_settings()
    if s.tts_engine in SERVER_ENGINES:
        return s.tts_engine
    return s.tts_replay_engine or ("flite" if shutil.which("ffmpeg") else "stub")


def engine_status() -> dict:
    s = get_settings()
    return {
        "default": s.tts_engine, "replay": replay_engine(),
        "flite": {"available": bool(shutil.which("ffmpeg"))},
        "piper": {"available": bool(shutil.which("piper")) and (s.models_dir / "piper" / f"{s.piper_voice}.onnx").exists()},
        "openai": {"available": bool(s.openai_api_key), "model": s.openai_tts_model, "voice": s.openai_tts_voice},
        "sarvam": {"available": bool(s.sarvam_api_key), "model": s.sarvam_tts_model, "voice": s.sarvam_tts_voice},
        "browser": {"available": True},
    }


def wav_of(pcm: bytes) -> bytes:
    return wav_bytes(pcm)
