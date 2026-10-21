"""System status, cost so far, and the Speech lab (record test clips, measure recognition accuracy per engine)."""
from __future__ import annotations

import json
import time
import uuid

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..config import DATA_FILES, get_settings
from ..db import audit, get_db, now_iso
from ..llm.providers import provider_status, test_connection
from ..voice import stt as stt_mod
from ..voice import tts as tts_mod
from ..voice.audio import SR, decode_to_pcm, read_secure, wav_bytes, write_secure
from ..voice.vad import model_available
from ..voice.wer import corpus_wer, term_recall, wer
from ..web import ApiError, Ctx, Router, not_found

router = Router("/api")
PRICING = json.loads((DATA_FILES / "pricing.json").read_text(encoding="utf-8"))


def _pct(xs: list, p: float):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


@router.get("/system/status", auth="staff")
def status(ctx: Ctx):
    from ..live import ACTIVE

    db = get_db()
    s = get_settings()
    recent = db.fetchall("SELECT latency FROM sessions WHERE latency IS NOT NULL AND latency != '{}' ORDER BY id DESC LIMIT 200")
    samples = [x for r in recent for x in (r.get("latency") or {}).get("samples", [])]
    queue = db.fetchone("SELECT SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS q, SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS r, "
                        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS f FROM tasks")
    return {
        "ai": provider_status(), "stt": stt_mod.engine_status(), "tts": tts_mod.engine_status(),
        "vad": {"silero": model_available(s.models_dir)},
        "turn_taking": {"endpoint_silence_ms": s.endpoint_silence_ms, "endpoint_incomplete_ms": s.endpoint_incomplete_ms},
        "live_sessions": len(ACTIVE), "stt_queue": stt_mod.queue_depth(),
        "tasks": {"queued": queue["q"] or 0, "running": queue["r"] or 0, "failed": queue["f"] or 0},
        "latency": {"replies": len(samples), "server_p50_ms": _pct([x.get("server_ms") for x in samples], 0.5),
                    "server_p95_ms": _pct([x.get("server_ms") for x in samples], 0.95),
                    "client_p50_ms": _pct([x.get("client_ms") for x in samples], 0.5),
                    "client_p95_ms": _pct([x.get("client_ms") for x in samples], 0.95),
                    "stt_p50_ms": _pct([x.get("stt_ms") for x in samples], 0.5)},
        "privacy": {"encrypt_audio": s.encrypt_audio, "retention_days": s.retention_days},
    }


@router.post("/system/test-ai", auth="staff", rate=(10, 600))
async def test_ai(ctx: Ctx):
    return await test_connection()


@router.get("/system/cost", auth="staff")
def cost(ctx: Ctx):
    """Actual spend from the usage ledger, per finished viva, projected to a batch of 350 students."""
    db = get_db()
    rows = db.fetchall("SELECT feature, provider, model, COUNT(*) AS calls, SUM(input_tokens) AS tin, SUM(output_tokens) AS tout, "
                       "SUM(audio_seconds) AS audio, SUM(characters) AS chars, SUM(cost_usd) AS usd FROM usage_ledger GROUP BY feature, provider, model")
    finished = db.scalar("SELECT COUNT(*) FROM sessions WHERE status IN ('scored', 'reviewed')") or 0
    per_session = db.fetchall("SELECT session_id, SUM(cost_usd) AS usd FROM usage_ledger WHERE session_id IS NOT NULL GROUP BY session_id")
    shared = sum(r["usd"] or 0 for r in rows) - sum(r["usd"] or 0 for r in per_session)
    avg = (sum(r["usd"] or 0 for r in per_session) / len(per_session)) if per_session else 0.0
    inr = PRICING["usd_to_inr"]
    return {"by_feature": rows, "finished_vivas": finished, "avg_usd_per_viva": round(avg, 5), "avg_inr_per_viva": round(avg * inr, 2),
            "shared_usd": round(shared, 4), "projection_350_inr": round((avg * 350 + shared) * inr, 0), "usd_to_inr": inr}


# ----------------------------------------------------------------------------- speech lab
@router.get("/system/audit", auth="admin")
def audit_log(ctx: Ctx):
    """The whole append-only audit log, newest first (administrators)."""
    from .vivas import AUDIT_TEXT

    limit = min(500, int(ctx.query.get("limit") or 200))
    action = ctx.query.get("action") or ""
    db = get_db()
    rows = db.fetchall("SELECT * FROM audit_log " + ("WHERE action = ? " if action else "") + "ORDER BY id DESC LIMIT ?",
                       ((action, limit) if action else (limit,)))
    return {"entries": [{"id": r["id"], "at": r["created_at"], "who": r["actor_name"], "action": r["action"],
                         "what": AUDIT_TEXT.get(r["action"], r["action"].replace("_", " ")), "entity": r["entity"],
                         "entity_id": r["entity_id"], "data": r.get("data") or {}} for r in rows]}


@router.get("/lab/prompts", auth="staff")
def prompts(ctx: Ctx):
    return json.loads((DATA_FILES / "stt_prompts.json").read_text(encoding="utf-8"))


@router.get("/lab/clips", auth="staff")
def clips(ctx: Ctx):
    rows = get_db().fetchall("SELECT id, speaker, accent, language, reference, duration_s, created_at FROM stt_clips ORDER BY id DESC")
    return {"clips": rows, "engines": stt_mod.engine_status()}


@router.post("/lab/clips", auth="staff", rate=(200, 3600))
def add_clip(ctx: Ctx):
    f = ctx.files.get("audio")
    ref = (ctx.form.get("reference") or "").strip()
    if not f or not ref:
        raise ApiError(422, "Audio and reference text are required", "invalid")
    if str(ctx.form.get("consent", "")).lower() not in ("1", "true", "yes", "on"):
        raise ApiError(422, "The speaker must consent to the recording being used for testing", "consent_required")
    try:
        pcm = decode_to_pcm(f["data"])
    except Exception as exc:
        raise ApiError(422, f"Could not read the audio: {exc}"[:200], "bad_audio")
    if len(pcm) < SR:        # under half a second of audio
        raise ApiError(422, "The recording is too short", "bad_audio")
    rel = f"lab/{uuid.uuid4().hex}.wav"
    write_secure(get_settings().data_dir / rel, wav_bytes(pcm))
    cid = get_db().insert("stt_clips", {"speaker": (ctx.form.get("speaker") or "volunteer")[:60], "accent": (ctx.form.get("accent") or "")[:60],
                                        "language": ctx.form.get("language") or "en-IN", "reference": ref[:1000], "audio_path": rel,
                                        "duration_s": round(len(pcm) / 2 / SR, 2), "consent": 1, "created_by": ctx.uid,
                                        "created_at": now_iso()})
    audit(ctx.user, "lab_clip", "stt_clip", cid)
    return {"id": cid}


@router.delete("/lab/clips/{cid:int}", auth="staff")
def delete_clip(ctx: Ctx):
    row = get_db().fetchone("SELECT * FROM stt_clips WHERE id = ?", (int(ctx.path["cid"]),))
    if not row:
        raise not_found("Clip")
    (get_settings().data_dir / row["audio_path"]).unlink(missing_ok=True)
    get_db().execute("DELETE FROM stt_clips WHERE id = ?", (row["id"],))
    return {"ok": True}


class EvalIn(BaseModel):
    engines: list[str] = Field(default_factory=lambda: [get_settings().stt_engine])
    with_prompt: bool = True


@router.post("/lab/evaluate", auth="staff", rate=(10, 3600))
async def evaluate(ctx: Ctx):
    body = ctx.parse(EvalIn)
    rows = get_db().fetchall("SELECT * FROM stt_clips ORDER BY id")
    if not rows:
        raise ApiError(422, "Record some clips first", "no_clips")
    terms = json.loads((DATA_FILES / "stt_prompts.json").read_text(encoding="utf-8"))["terms"]
    prompt = ", ".join(terms) if body.with_prompt else ""
    out = {}
    for engine in body.engines:
        if engine not in stt_mod.ENGINES or engine in ("browser", "stub"):
            continue
        per, pairs, kept, present, ms = [], [], 0, 0, []
        for r in rows:
            pcm = decode_to_pcm(read_secure(get_settings().data_dir / r["audio_path"]))
            lang = "hi" if r["language"] == "hi-en" and engine == "sarvam" else "en"
            t0 = time.perf_counter()
            res = await stt_mod.transcribe(pcm, engine=engine, language=lang, prompt=prompt)
            ms.append(int((time.perf_counter() - t0) * 1000))
            w = wer(r["reference"], res.text)
            k, p = term_recall(r["reference"], res.text, terms)
            kept, present = kept + k, present + p
            pairs.append((r["reference"], res.text))
            per.append({"clip": r["id"], "language": r["language"], "speaker": r["speaker"], "reference": r["reference"],
                        "hypothesis": res.text, "wer": round(w["wer"], 3), "error": res.error})
        by_lang = {}
        for lang in ("en-IN", "hi-en"):
            lp = [(p["reference"], p["hypothesis"]) for p in per if p["language"] == lang]
            if lp:
                by_lang[lang] = round(corpus_wer(lp)["wer"], 3)
        out[engine] = {"wer": round(corpus_wer(pairs)["wer"], 3), "by_language": by_lang, "term_recall": round(kept / max(1, present), 3),
                       "p50_ms": sorted(ms)[len(ms) // 2] if ms else None, "clips": per}
    audit(ctx.user, "lab_evaluate", "stt", "", data={k: v["wer"] for k, v in out.items()})
    return {"results": out, "n_clips": len(rows), "prompted": body.with_prompt}


@router.get("/lab/clips/{cid:int}/audio", auth="staff")
def clip_audio(ctx: Ctx):
    row = get_db().fetchone("SELECT * FROM stt_clips WHERE id = ?", (int(ctx.path["cid"]),))
    if not row:
        raise not_found("Clip")
    return Response(read_secure(get_settings().data_dir / row["audio_path"]), media_type="audio/wav")
