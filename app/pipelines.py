"""Background tasks: finishing a viva's recording, scoring, pre-synthesising speech, bank generation, housekeeping."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .config import get_settings
from .db import get_db, iso_in, now_iso
from .engine import phrases
from .tasks import enqueue, handler, schedule
from .voice import tts as tts_mod
from .voice.audio import SR, Recording, decode_to_pcm, encode_pcm, float_to_pcm, pcm_to_float, waveform_peaks, write_secure

log = logging.getLogger("vaani.pipelines")


def viva_texts(viva_id: int) -> list[str]:
    from .bank.service import questions_of

    viva = get_db().fetchone("SELECT * FROM vivas WHERE id = ?", (viva_id,))
    texts = list(phrases.all_cacheable())
    topics = set()
    for q in questions_of(viva["bank_id"], "approved"):
        texts += [q["question"], q.get("rephrase") or "", *q.get("hints", []), *[f["text"] for f in q.get("follow_ups", [])],
                  *[m.get("hint", "") for m in q.get("misconceptions", [])]]
        topics.add(q["topic"])
    texts += [phrases.depth_intro(t) for t in topics]
    seen, out = set(), []
    for t in texts:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


@handler("presynth_viva")
async def presynth_viva(task: dict) -> dict:
    from .vivas import get_viva, viva_settings

    viva = get_viva(task["payload"]["viva_id"])
    engine = viva_settings(viva)["tts_engine"]
    if engine not in tts_mod.SERVER_ENGINES:
        engine = tts_mod.replay_engine()          # browser voice live; the replay voice is prepared instead
    texts = viva_texts(viva["id"])
    t0 = time.perf_counter()
    made = failed = 0
    for i, text in enumerate(texts):
        try:
            await tts_mod.get_clip(text, engine)
            made += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("presynth failed for %r: %s", text[:40], exc)
        if i % 20 == 0:
            from .tasks import set_progress

            set_progress(task["id"], int(100 * i / max(1, len(texts))), f"{i}/{len(texts)} clips")
    return {"engine": engine, "clips": made, "failed": failed, "seconds": round(time.perf_counter() - t0, 1)}


def _background_priority() -> None:
    """Run this thread (and any ffmpeg it starts) at lower CPU priority than the live vivas (Linux: per thread)."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
        pass


# Post-viva work (decrypting, mixing, Opus encoding, integrity analysis) is CPU-heavy. It must never run on the event
# loop, where it would stall every live viva on this server (measured: a 15-minute mix-down blocked replies for
# up to 17 s), so it runs on its own low-priority threads.
_bg_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="post-viva", initializer=_background_priority)


async def in_background(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_bg_pool, fn, *args)


async def build_recording(session_id: int) -> dict:
    """Mix the student's track with the examiner's voice at the right moments -> conversation.ogg (+ waveform)."""
    db = get_db()
    sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    turns = db.fetchall("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (session_id,))
    live_engine = (sess.get("settings") or {}).get("tts_engine") or get_settings().tts_engine
    engine = live_engine if live_engine in tts_mod.SERVER_ENGINES else tts_mod.replay_engine()
    clips: dict[str, str | None] = {}                # examiner text -> cached clip file (fetched/synthesised here)
    for t in turns:
        if t["speaker"] != "examiner":
            continue
        for seg in (t.get("meta") or {}).get("segments") or [{"text": t["text"]}]:
            text = seg.get("text", "")
            if text and text not in clips:
                try:
                    clips[text] = str(get_settings().tts_dir / (await tts_mod.get_clip(text, engine, session_id))["path"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("replay clip failed: %s", exc)
                    clips[text] = None
    info = await in_background(_mix_recording, session_id, turns, clips)
    info.update({"replay_voice": engine, "examiner_voice_rebuilt": live_engine not in tts_mod.SERVER_ENGINES})
    return info


def _mix_recording(session_id: int, turns: list[dict], clips: dict[str, str | None]) -> dict:
    student = Recording(session_id).read()
    stu = pcm_to_float(student)
    examiner_parts = []
    cache: dict[str, np.ndarray] = {}
    for i, t in enumerate(turns):
        if t["speaker"] != "examiner":
            continue
        nxt_student = next((x["t_start"] for x in turns[i + 1:] if x["speaker"] == "student"), None)
        pos = t["t_start"]
        for seg in (t.get("meta") or {}).get("segments") or [{"text": t["text"]}]:
            text = seg.get("text", "")
            if not text:
                continue
            if text not in cache:
                try:
                    cache[text] = pcm_to_float(decode_to_pcm(open(clips[text], "rb").read())) if clips.get(text) else \
                        np.zeros(int(SR * len(text) / 15), np.float32)
                except Exception as exc:  # noqa: BLE001
                    log.warning("replay clip decode failed: %s", exc)
                    cache[text] = np.zeros(int(SR * len(text) / 15), np.float32)
            a = cache[text]
            if nxt_student is not None and pos + len(a) / SR > nxt_student + 0.3:      # barge-in: cut where the student spoke
                a = a[: max(0, int((nxt_student + 0.3 - pos) * SR))]
            examiner_parts.append((pos, a))
            pos += len(a) / SR + 0.15
    end = max([len(stu) / SR] + [p + len(a) / SR for p, a in examiner_parts] + [0.0])
    mix = np.zeros(int(end * SR) + SR // 2, np.float32)
    mix[: len(stu)] += stu
    for p, a in examiner_parts:
        s = int(p * SR)
        if s < len(mix):
            n = min(len(a), len(mix) - s)
            mix[s:s + n] += 0.85 * a[:n]
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.99:
        mix /= peak / 0.99
    d = get_settings().sessions_dir / str(session_id)
    conv, mime = encode_pcm(float_to_pcm(mix), "ogg")
    write_secure(d / "conversation.ogg", conv)
    if student:
        s_ogg, _ = encode_pcm(student, "ogg")
        write_secure(d / "student.ogg", s_ogg)
    (d / "waveform.json").write_text(json.dumps({"duration": round(len(mix) / SR, 2), "peaks": waveform_peaks(float_to_pcm(mix))}))
    return {"duration_s": round(len(mix) / SR, 1), "mime": mime, "bytes": len(conv)}


def _analyse_integrity(session_id: int) -> list:
    from . import integrity

    return integrity.analyse(session_id, Recording(session_id).read())


@handler("finalize_session")
async def finalize_session(task: dict) -> dict:
    sid = task["payload"]["session_id"]
    db = get_db()
    info = await build_recording(sid)
    events = await in_background(_analyse_integrity, sid)
    rec = Recording(sid)
    if not get_settings().keep_raw_pcm and (get_settings().sessions_dir / str(sid) / "student.ogg").exists():
        rec.path.unlink(missing_ok=True)
    sess = db.fetchone("SELECT client FROM sessions WHERE id = ?", (sid,))
    db.update("sessions", sid, {"client": {**(sess.get("client") or {}), "recording": info}})
    enqueue("score_session", {"session_id": sid}, dedupe_key=f"score:{sid}")
    return {"recording": info, "integrity_events": len(events)}


@handler("score_session")
async def score_session_task(task: dict) -> dict:
    from .scoring.service import score_session

    return await score_session(task["payload"]["session_id"])


@handler("rescore_session")
async def rescore_session_task(task: dict) -> dict:
    from .scoring.service import score_session

    return await score_session(task["payload"]["session_id"])


@handler("generate_bank")
async def generate_bank(task: dict) -> dict:
    from .bank.generator import generate_from_syllabus
    from .bank.service import add_questions, get_bank

    p = task["payload"]
    bank = get_bank(p["bank_id"])
    res = await generate_from_syllabus(bank["subject"], p["syllabus"], p.get("counts") or {"fundamentals": 6, "depth": 3, "applied": 2},
                                       bank.get("aliases"))
    added, _ = add_questions(bank["id"], res["questions"], source="ai", status="draft", ai_meta=res["meta"]) if res["questions"] else (0, 0)
    return {"drafts": added, "errors": res["errors"], "offline": res.get("offline", False), "meta": res["meta"]}


@handler("sweep_sessions")
def sweep_sessions(task: dict) -> dict:
    from .live import sweep_stale_sessions

    return {"ended": sweep_stale_sessions()}


@handler("cleanup")
def cleanup(task: dict) -> dict:
    """Retention: delete recordings older than RETENTION_DAYS (transcripts and scores stay for the academic record)."""
    db = get_db()
    cutoff = iso_in(days=-get_settings().retention_days)
    n = 0
    for s in db.fetchall("SELECT id FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,)):
        d = get_settings().sessions_dir / str(s["id"])
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    db.execute("DELETE FROM llm_cache WHERE created_at < ?", (iso_in(days=-30),))
    db.execute("DELETE FROM tasks WHERE status = 'done' AND finished_at < ?", (iso_in(days=-7),))
    return {"recordings_deleted": n, "at": now_iso()}


schedule("sweep", 30, "sweep_sessions")
schedule("cleanup", 24 * 3600, "cleanup")
