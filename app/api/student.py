"""Student endpoints: the viva the student joined, consent, and a microphone check."""
from __future__ import annotations

from pydantic import BaseModel

from ..db import get_db
from ..vivas import CONSENT_VERSION, consent_text, get_session, get_viva, record_consent
from ..web import ApiError, Ctx, Router, client_ip

router = Router("/api/student")

RULES = [
    "The viva lasts at most 15 minutes. The timer on screen shows the time left.",
    "It has five parts: a short warm-up about your project, fundamentals, a deeper discussion, a practical problem, and a wrap-up.",
    "Answer out loud in your own words. English or Hinglish are both fine; your accent never affects your marks.",
    "Saying \"I don't know\" is fine: the examiner simply moves on. You can say \"please repeat\" or press Repeat at any time.",
    "When you finish an answer, pause or press \"I'm done\". Each answer is limited to 90 seconds.",
    "Stay on this tab and do not use notes or other people. Switching tabs and long silences are noted for your faculty.",
    "Use headphones if you can, in a quiet room. If your connection drops, reopen the page: you will continue where you left off.",
]


def _student_session(ctx: Ctx) -> dict:
    sid = None
    from ..security import SESSION_COOKIE, decode_session_token

    token = ctx.request.cookies.get(SESSION_COOKIE) or ctx.request.headers.get("authorization", "")[7:]
    payload = decode_session_token(token) if token else None
    if payload:
        sid = payload.get("sid")
    row = get_session(int(sid)) if sid else get_db().fetchone(
        "SELECT * FROM sessions WHERE student_id = ? ORDER BY id DESC LIMIT 1", (ctx.uid,))
    if not row or row["student_id"] != ctx.uid:
        raise ApiError(404, "No viva found. Join with the code from your faculty.", "no_session")
    return row


@router.get("/session", auth="student")
def my_session(ctx: Ctx):
    sess = _student_session(ctx)
    viva = get_viva(sess["viva_id"])
    st = sess.get("state") or {}
    return {
        "session": {"id": sess["id"], "status": sess["status"], "consented": bool((sess.get("consent") or {}).get("accepted_at")),
                    "started": bool(sess.get("started_epoch")), "end_reason": sess.get("end_reason"),
                    "settings": {k: (sess.get("settings") or {}).get(k) for k in ("stt_engine", "tts_engine", "language", "code_round",
                                                                                   "barge_in", "allow_end_early")},
                    "phase": st.get("phase")},
        "viva": {"title": viva["title"], "subject": viva["subject"], "duration_s": viva["duration_s"]},
        "student": {"name": ctx.user["name"], "roll_no": ctx.user["roll_no"]},
        "rules": RULES, "consent_text": consent_text(), "consent_version": CONSENT_VERSION,
    }


SAMPLE_TEXT = "Hello. This is how your examiner will sound. If you can hear me clearly, press I heard it."


@router.get("/voice-sample", auth="student")
async def voice_sample(ctx: Ctx):
    from starlette.concurrency import run_in_threadpool

    from ..voice import tts as tts_mod

    sess = await run_in_threadpool(_student_session, ctx)
    engine = (sess.get("settings") or {}).get("tts_engine")
    clip = None
    if engine in tts_mod.SERVER_ENGINES:
        try:
            clip = await tts_mod.get_clip(SAMPLE_TEXT, engine)
        except Exception:  # noqa: BLE001 - the browser voice is the fallback
            clip = None
    return {"text": SAMPLE_TEXT, "audio": tts_mod.clip_url(clip), "engine": engine}


class ConsentIn(BaseModel):
    accept: bool
    client: dict = {}


@router.post("/consent", auth="student")
def consent(ctx: Ctx):
    body = ctx.parse(ConsentIn)
    sess = _student_session(ctx)
    client = {k: str(v)[:200] for k, v in (body.client or {}).items()}
    client.update({"ip": client_ip(ctx.request), "ua": ctx.request.headers.get("user-agent", "")[:200]})
    try:
        sess = record_consent(sess, body.accept, client)
    except ValueError as exc:
        raise ApiError(422, str(exc), "consent_required")
    return {"ok": True, "status": sess["status"]}
