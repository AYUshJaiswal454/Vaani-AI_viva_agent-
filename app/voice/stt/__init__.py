"""Speech-to-text engines behind one interface.

    whisper-onnx    built in: Whisper via onnxruntime + numpy, runs on the server's CPU, no API cost
    faster-whisper  optional package (pip install faster-whisper), CTranslate2, fastest local option on CPU/GPU
    openai          OpenAI transcription API (gpt-4o-mini-transcribe by default)
    groq            Groq's Whisper large-v3-turbo (OpenAI-compatible API), very fast and cheap
    sarvam          Sarvam AI (saaras), built for Indian languages, Indian-accented English and Hinglish
    browser         the student's browser recognises speech and sends text (no server cost; Chrome sends audio to Google)
    stub            load tests only: returns the text a test bot declared, after a configurable delay

Every engine receives the same subject vocabulary prompt (key terms from the question bank), which improves
recognition of technical words such as "B+ tree" or "serializable".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ...config import DATA_FILES, get_settings
from ...llm.providers import record_usage
from ...net.http import client
from ..audio import SR, pcm_to_float, wav_bytes
from .whisper_onnx import Cancelled

log = logging.getLogger("vaani.stt")
PRICING = json.loads((DATA_FILES / "pricing.json").read_text(encoding="utf-8"))

ENGINES = ("whisper-onnx", "faster-whisper", "openai", "groq", "sarvam", "browser", "stub")


@dataclass
class STTResult:
    text: str
    confidence: float | None
    engine: str
    model: str
    compute_ms: int
    audio_s: float
    language: str = "en"
    error: str = ""
    fallback_from: str = ""       # the hosted engine that failed, when the local recogniser stepped in

    def to_meta(self) -> dict:
        return {"engine": self.engine, "model": self.model, "confidence": None if self.confidence is None else round(self.confidence, 3),
                "compute_ms": self.compute_ms, "audio_s": round(self.audio_s, 2), "language": self.language,
                **({"error": self.error} if self.error else {}), **({"fallback_from": self.fallback_from} if self.fallback_from else {})}


_pool: ThreadPoolExecutor | None = None
_pool_sem: dict[int, asyncio.Semaphore] = {}


def workers() -> int:
    """Recognition workers on this server (STT_CONCURRENCY, default: CPU cores / WHISPER_THREADS)."""
    s = get_settings()
    return s.stt_concurrency or max(1, (os.cpu_count() or 2) // max(1, s.whisper_threads))


def _local_available() -> bool:
    from .whisper_onnx import find_model_dir

    s = get_settings()
    return bool(find_model_dir(s.models_dir, s.whisper_model))


def local_pool():
    from .whisper_onnx import get_pool

    s = get_settings()
    return get_pool(s.models_dir, s.whisper_model, size=workers(), threads=s.whisper_threads)


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=workers(), thread_name_prefix="stt")
    return _pool


def queue_depth() -> int:
    p = _pool
    return p._work_queue.qsize() if p else 0     # noqa: SLF001 - monitoring only


def _cost(engine: str, model: str, audio_s: float) -> float:
    if engine in ("openai", "groq"):
        return PRICING["stt_per_minute_usd"].get(model, 0.0) * audio_s / 60
    if engine == "sarvam":
        return PRICING["sarvam_inr"]["stt_per_hour"] * audio_s / 3600 / PRICING["usd_to_inr"]
    return 0.0


async def transcribe(pcm: bytes, *, engine: str | None = None, language: str | None = None, prompt: str = "",
                     session_id: int | None = None, hint: str | None = None, cancel: threading.Event | None = None) -> STTResult:
    """`cancel` lets the live session abandon a speculative recognition the moment the student resumes speaking,
    so the CPU goes back to work that is still needed (local Whisper stops within one decoder step)."""
    s = get_settings()
    engine = engine or s.stt_engine
    language = language or s.stt_language
    audio_s = len(pcm) / 2 / SR
    t0 = time.perf_counter()
    if cancel is not None and cancel.is_set():
        return STTResult("", None, engine, "", 0, audio_s, error="cancelled")
    try:
        if engine == "whisper-onnx":
            res = await _whisper_onnx(pcm, language, prompt, cancel)
        elif engine == "faster-whisper":
            res = await _faster_whisper(pcm, language, prompt)
        elif engine in ("openai", "groq"):
            res = await _openai_compat(pcm, engine, language, prompt)
        elif engine == "sarvam":
            res = await _sarvam(pcm, language, prompt)
        elif engine == "stub":
            await asyncio.sleep(s.stub_stt_latency_ms / 1000 * (0.7 + 0.6 * (hash(hint or "") % 100) / 100))
            res = STTResult(hint or "", 0.9 if hint else 0.0, "stub", "stub", 0, audio_s)
        else:
            return STTResult("", None, engine, "", 0, audio_s, error=f"{engine} recognition happens in the browser")
    except Cancelled:
        return STTResult("", None, engine, "", int((time.perf_counter() - t0) * 1000), audio_s, error="cancelled")
    except Exception as exc:  # noqa: BLE001 - recognition failure must never crash a viva
        log.warning("STT %s failed: %s", engine, exc)
        res = STTResult("", None, engine, "", int((time.perf_counter() - t0) * 1000), audio_s, error=f"{type(exc).__name__}: {exc}"[:300])
    if res.error and engine in ("groq", "openai", "sarvam") and _local_available():
        # A hosted recogniser failed (network, quota, outage): recognise this answer locally instead of losing it.
        try:
            local = await _whisper_onnx(pcm, language if language != "hi" else "auto", prompt, cancel)
            local.fallback_from = f"{engine}: {res.error[:120]}"
            res = local
        except Exception as exc:  # noqa: BLE001
            log.warning("local fallback failed: %s", exc)
    res.compute_ms = int((time.perf_counter() - t0) * 1000)
    res.audio_s = audio_s
    record_usage(session_id, "stt", engine if engine in ("openai", "groq", "sarvam") else "local", res.model or engine,
                 {}, res.compute_ms, audio_seconds=audio_s, cost=_cost(engine, res.model, audio_s))
    return res


async def _whisper_onnx(pcm: bytes, language: str, prompt: str, cancel: threading.Event | None = None) -> STTResult:
    s = get_settings()
    model = local_pool()
    lang = "en" if model.is_multilingual is False else ({"auto": "auto"}.get(language, language))
    loop = asyncio.get_running_loop()
    tr = await loop.run_in_executor(_executor(), model.transcribe, pcm_to_float(pcm), lang, prompt, cancel)
    return STTResult(tr.text, tr.confidence, "whisper-onnx", f"whisper-{s.whisper_model}", tr.compute_ms, tr.duration_s, tr.language)


_fw_model = None


async def _faster_whisper(pcm: bytes, language: str, prompt: str) -> STTResult:
    global _fw_model
    from faster_whisper import WhisperModel  # type: ignore

    s = get_settings()
    name = os.environ.get("FASTER_WHISPER_MODEL", "small")
    if _fw_model is None:
        _fw_model = WhisperModel(name, device=os.environ.get("FASTER_WHISPER_DEVICE", "auto"),
                                 compute_type=os.environ.get("FASTER_WHISPER_COMPUTE", "int8"))

    def run():
        import math

        segs, info = _fw_model.transcribe(pcm_to_float(pcm), language=None if language == "auto" else language,
                                          initial_prompt=prompt or None, beam_size=1, vad_filter=False,
                                          condition_on_previous_text=False)
        segs = list(segs)
        text = " ".join(x.text.strip() for x in segs).strip()
        lp = [x.avg_logprob for x in segs] or [-3.0]
        return text, math.exp(sum(lp) / len(lp)), info.language

    text, conf, lang = await asyncio.get_running_loop().run_in_executor(_executor(), run)
    return STTResult(text, conf, "faster-whisper", f"faster-whisper-{name}", 0, len(pcm) / 2 / SR, lang)


async def _openai_compat(pcm: bytes, engine: str, language: str, prompt: str) -> STTResult:
    s = get_settings()
    if engine == "groq":
        base, key, model = s.groq_base_url, s.groq_api_key, s.groq_stt_model
    else:
        base, key, model = s.openai_base_url, s.openai_api_key, s.openai_stt_model
    if not key:
        raise RuntimeError(f"{engine.upper()}_API_KEY is not set")
    data = {"model": model, "response_format": "verbose_json" if engine == "groq" else "json"}
    if language and language not in ("auto",):
        data["language"] = language
    if prompt:
        data["prompt"] = prompt[:800]
    files = {"file": ("answer.wav", wav_bytes(pcm), "audio/wav")}
    resp = await client().post(base.rstrip("/") + "/audio/transcriptions", data=data, files=files,
                               headers={"authorization": f"Bearer {key}"}, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    conf = None
    segs = body.get("segments") or []
    if segs:
        import math

        lps = [x.get("avg_logprob", -1.0) for x in segs]
        conf = math.exp(sum(lps) / len(lps))
    return STTResult((body.get("text") or "").strip(), conf, engine, model, 0, len(pcm) / 2 / SR, body.get("language") or language)


async def _sarvam(pcm: bytes, language: str, prompt: str) -> STTResult:
    s = get_settings()
    if not s.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is not set")
    lang = {"en": "en-IN", "hi": "hi-IN", "auto": "unknown"}.get(language, language)
    data = {"model": s.sarvam_stt_model, "language_code": lang}
    if prompt and "v4" in s.sarvam_stt_model:
        terms = [t.strip() for t in prompt.split(",") if t.strip()][:50]
        data["keyterms"] = json.dumps([t[:64] for t in terms])
    resp = await client().post("https://api.sarvam.ai/speech-to-text", data=data,
                               files={"file": ("answer.wav", wav_bytes(pcm), "audio/wav")},
                               headers={"api-subscription-key": s.sarvam_api_key}, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    return STTResult((body.get("transcript") or "").strip(), body.get("language_probability"), "sarvam", s.sarvam_stt_model, 0,
                     len(pcm) / 2 / SR, body.get("language_code") or lang)


def engine_status() -> dict:
    """What can run here (shown on the faculty settings page and /api/health)."""
    s = get_settings()
    from .whisper_onnx import find_model_dir

    try:
        import faster_whisper  # type: ignore  # noqa: F401
        fw = True
    except Exception:
        fw = False
    return {
        "default": s.stt_engine,
        "whisper-onnx": {"available": bool(find_model_dir(s.models_dir, s.whisper_model)), "model": s.whisper_model,
                         "workers": workers(), "threads_per_worker": s.whisper_threads},
        "faster-whisper": {"available": fw},
        "openai": {"available": bool(s.openai_api_key), "model": s.openai_stt_model},
        "groq": {"available": bool(s.groq_api_key), "model": s.groq_stt_model},
        "sarvam": {"available": bool(s.sarvam_api_key), "model": s.sarvam_stt_model},
        "browser": {"available": True, "note": "Chrome/Edge only; Chrome sends audio to Google for recognition"},
    }
