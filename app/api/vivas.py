"""Faculty: create and run vivas; list attempts; review a viva (audio, transcript, scores, override); export."""
from __future__ import annotations

import csv
import io
import json

from pydantic import BaseModel, Field
from starlette.responses import Response

from .. import integrity
from ..bank.service import get_bank
from ..db import audit, get_db
from ..engine.examiner import DEFAULT_PHASES
from ..scoring import service as scoring
from ..scoring.rubric import DIMENSIONS, default_rubric, validate_rubric, weights
from ..scoring.transcript import line_id, mmss
from ..security import signed_token, verify_signed_token
from ..tasks import enqueue
from ..vivas import DEFAULT_SETTINGS, create_viva, get_viva, set_status, update_viva, viva_settings
from ..web import ApiError, Ctx, Router, not_found

router = Router("/api")


def _viva(ctx: Ctx, key: str = "viva_id") -> dict:
    v = get_viva(int(ctx.path[key]))
    if not v or (v["owner_id"] != ctx.uid and not ctx.is_admin):
        raise not_found("Viva")
    return v


def _viva_summary(v: dict) -> dict:
    db = get_db()
    c = db.fetchone(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN status IN ('scored','reviewed') THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS review, SUM(CASE WHEN status = 'live' THEN 1 ELSE 0 END) AS live, "
        "AVG(final_total) AS avg FROM sessions WHERE viva_id = ?", (v["id"],))
    bank = get_bank(v["bank_id"])
    return {"id": v["id"], "title": v["title"], "subject": v["subject"], "status": v["status"], "access_code": v["access_code"],
            "bank": {"id": bank["id"], "name": bank["name"], "version": bank["version"]}, "difficulty": v["difficulty"],
            "topics": v.get("topics") or [], "settings": viva_settings(v), "phase_plan": v.get("phase_plan") or {},
            "rubric": validate_rubric(v.get("rubric")), "roster": v.get("roster") or [], "opens_at": v.get("opens_at"),
            "closes_at": v.get("closes_at"), "attempts": c["n"] or 0, "scored": c["done"] or 0, "needs_review": c["review"] or 0,
            "live": c["live"] or 0, "average": round(c["avg"], 1) if c["avg"] is not None else None, "created_at": v["created_at"]}


@router.get("/vivas", auth="staff")
def list_vivas(ctx: Ctx):
    rows = get_db().fetchall("SELECT * FROM vivas WHERE owner_id = ? OR ? ORDER BY created_at DESC", (ctx.uid, int(ctx.is_admin)))
    return {"vivas": [_viva_summary(v) for v in rows], "defaults": {"settings": DEFAULT_SETTINGS, "phases": DEFAULT_PHASES,
                                                                     "rubric": default_rubric()}}


@router.post("/vivas", auth="staff")
def create(ctx: Ctx):
    data = ctx.body or {}
    bank = get_bank(int(data.get("bank_id") or 0))
    if not bank or (bank["owner_id"] != ctx.uid and not ctx.is_admin):
        raise ApiError(422, "Choose one of your question banks", "invalid")
    try:
        v = create_viva(ctx.user, data)
    except ValueError as exc:
        raise ApiError(422, str(exc), "invalid")
    return {"viva": _viva_summary(v)}


@router.get("/vivas/{viva_id:int}", auth="staff")
def get(ctx: Ctx):
    return {"viva": _viva_summary(_viva(ctx))}


@router.patch("/vivas/{viva_id:int}", auth="staff")
def update(ctx: Ctx):
    v = _viva(ctx)
    try:
        v = update_viva(v, ctx.body or {}, ctx.user)
    except ValueError as exc:
        raise ApiError(422, str(exc), "invalid")
    return {"viva": _viva_summary(v)}


class StatusIn(BaseModel):
    status: str


@router.post("/vivas/{viva_id:int}/status", auth="staff")
def status(ctx: Ctx):
    v = _viva(ctx)
    body = ctx.parse(StatusIn)
    if body.status not in ("open", "closed", "draft"):
        raise ApiError(422, "status must be open, closed or draft", "invalid")
    try:
        v = set_status(v, body.status, ctx.user)
    except ValueError as exc:
        raise ApiError(422, str(exc), "not_ready")
    return {"viva": _viva_summary(v)}


# ----------------------------------------------------------------------------- attempts
def _session_row(s: dict) -> dict:
    integ = integrity.summary(s["id"])
    return {"id": s["id"], "student": s["student_name"], "roll_no": s["roll_no"], "status": s["status"],
            "started_at": s.get("started_at"), "ended_at": s.get("ended_at"), "end_reason": s.get("end_reason"),
            "final_total": s.get("final_total"), "needs_review": bool(s.get("needs_review")), "integrity": integ,
            "duration_s": round((s.get("state") or {}).get("ended", {}).get("t", 0) or 0, 1) if s.get("state") else None}


@router.get("/vivas/{viva_id:int}/sessions", auth="staff")
def sessions(ctx: Ctx):
    v = _viva(ctx)
    rows = get_db().fetchall("SELECT s.*, u.name AS student_name, u.roll_no FROM sessions s JOIN users u ON u.id = s.student_id "
                             "WHERE s.viva_id = ? ORDER BY s.created_at DESC", (v["id"],))
    out = [_session_row(s) for s in rows]
    f = ctx.query.get("filter")
    if f == "review":
        out = [s for s in out if s["needs_review"]]
    elif f == "flagged":
        out = [s for s in out if s["integrity"]["warn"]]
    return {"viva": _viva_summary(v), "sessions": out}


def _session(ctx: Ctx, key: str = "session_id") -> tuple[dict, dict]:
    s = get_db().fetchone("SELECT s.*, u.name AS student_name, u.roll_no FROM sessions s JOIN users u ON u.id = s.student_id "
                          "WHERE s.id = ?", (int(ctx.path[key]),))
    if not s:
        raise not_found("Session")
    v = get_viva(s["viva_id"])
    if v["owner_id"] != ctx.uid and not ctx.is_admin:
        raise not_found("Session")
    return s, v


def _latency_stats(turns: list[dict]) -> dict:
    vals = [t["meta"]["latency"] for t in turns if t["speaker"] == "examiner" and (t.get("meta") or {}).get("latency")]
    def pct(xs, p):
        xs = sorted(x for x in xs if x is not None)
        return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None
    server = [v.get("server_ms") for v in vals]
    return {"replies": len(vals), "p50_ms": pct(server, 0.5), "p95_ms": pct(server, 0.95),
            "stt_p50_ms": pct([v.get("stt_ms") for v in vals], 0.5), "endpoint_ms": pct([v.get("endpoint_ms") for v in vals], 0.5)}


@router.get("/sessions/{session_id:int}", auth="staff")
def session_detail(ctx: Ctx):
    s, v = _session(ctx)
    db = get_db()
    turns = db.fetchall("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (s["id"],))
    scores = scoring.latest_scores(s["id"])
    blind = ctx.query.get("blind") == "1" and not db.fetchone(
        "SELECT id FROM faculty_marks WHERE session_id = ? AND faculty_id = ?", (s["id"], ctx.uid))
    if not blind and scores.get("ai"):
        audit(ctx.user, "ai_score_viewed", "session", s["id"])
    events = db.fetchall("SELECT * FROM integrity_events WHERE session_id = ? ORDER BY t", (s["id"],))
    marks = db.fetchone("SELECT * FROM faculty_marks WHERE session_id = ? AND faculty_id = ?", (s["id"], ctx.uid))
    st = s.get("state") or {}
    rec = (s.get("client") or {}).get("recording") or {}
    erased_at = rec.get("erased_at")
    if erased_at:
        rec = {}
    audio_token = signed_token(f"audio:{s['id']}", 6 * 3600)
    plan = s.get("plan") or {}
    usage = db.fetchone("SELECT COALESCE(SUM(cost_usd),0) AS usd, COALESCE(SUM(input_tokens),0) AS tin, "
                        "COALESCE(SUM(output_tokens),0) AS tout, COALESCE(SUM(audio_seconds),0) AS audio FROM usage_ledger "
                        "WHERE session_id = ?", (s["id"],))
    return {
        "session": {"id": s["id"], "student": s["student_name"], "roll_no": s["roll_no"], "status": s["status"],
                    "started_at": s.get("started_at"), "ended_at": s.get("ended_at"), "end_reason": s.get("end_reason"),
                    "needs_review": bool(s.get("needs_review")), "final_total": None if blind else s.get("final_total"),
                    "bank_version": s.get("bank_version"), "engine_version": s.get("engine_version"),
                    "settings": s.get("settings") or {}, "paused_s": s.get("paused_s"), "consent": s.get("consent") or {},
                    "phase_log": st.get("phase_log", []), "flags": st.get("flags", {}), "depth_topic": st.get("depth_topic"),
                    "recording": rec, "recording_erased_at": erased_at, "audio_url": f"/api/sessions/{s['id']}/audio?t={audio_token}" if rec else None,
                    "waveform_url": f"/api/sessions/{s['id']}/waveform?t={audio_token}" if rec else None,
                    "latency": _latency_stats(turns), "cost_usd": round(usage["usd"], 5),
                    "usage": {"input_tokens": usage["tin"], "output_tokens": usage["tout"], "audio_s": round(usage["audio"], 1)}},
        "viva": {"id": v["id"], "title": v["title"], "subject": v["subject"]},
        "can_erase": ctx.is_admin,
        "rubric": validate_rubric(v.get("rubric")),
        "turns": [{"seq": t["seq"], "id": line_id(t["seq"]), "speaker": t["speaker"], "kind": t["kind"], "phase": t.get("phase"),
                   "text": t["text"], "source": t.get("source"), "code": t.get("question_code"), "t": t["t_start"],
                   "t_end": t.get("t_end"), "mmss": mmss(t["t_start"]), "meta": _turn_meta(t)} for t in turns],
        "questions": {c: {k: q.get(k) for k in ("code", "topic", "phase", "type", "difficulty", "question", "key_points", "follow_ups",
                                                  "hints", "code_snippet")} for c, q in (plan.get("snapshot") or {}).items()},
        "scores": None if blind else _scores_out(scores),
        "blind": blind,
        "integrity": events,
        "my_marks": marks,
        "decisions": st.get("decisions", [])[-200:],
    }


def _turn_meta(t: dict) -> dict:
    m = t.get("meta") or {}
    out = {}
    if m.get("answer"):
        a = m["answer"]
        out["answer"] = {k: a.get(k) for k in ("label", "coverage", "covered", "reasoning", "words", "after_hint", "cut", "kind",
                                               "relevance", "generic", "fu")}
    for k in ("stt", "reason", "latency", "variant", "scratchpad", "duration_s", "segments"):
        if m.get(k) is not None:
            out[k] = m[k]
    return out


def _scores_out(scores: dict) -> dict:
    def clean(r):
        if not r:
            return None
        return {k: r.get(k) for k in ("id", "kind", "dims", "total", "feedback", "prompt_version", "model", "runs", "confidence",
                                      "needs_review", "review_reasons", "reviewer_name", "reason", "is_final", "created_at")}
    return {"ai": clean(scores.get("ai")), "rule": clean(scores.get("rule")), "override": clean(scores.get("override")),
            "final": clean(scores.get("final")), "history": scores.get("history")}


class OverrideIn(BaseModel):
    dims: dict[str, int | None]
    reason: str = Field(min_length=10, max_length=2000)


@router.post("/sessions/{session_id:int}/override", auth="staff")
def override(ctx: Ctx):
    s, _ = _session(ctx)
    body = ctx.parse(OverrideIn)
    try:
        row = scoring.override(s["id"], ctx.user, {k: v for k, v in body.dims.items() if k in DIMENSIONS}, body.reason)
    except ValueError as exc:
        raise ApiError(422, str(exc), "invalid")
    return {"score": _scores_out({"final": row})["final"]}


@router.post("/sessions/{session_id:int}/accept", auth="staff")
def accept(ctx: Ctx):
    s, _ = _session(ctx)
    scoring.accept_ai(s["id"], ctx.user, (ctx.body or {}).get("note", ""))
    return {"ok": True}


class MarksIn(BaseModel):
    dims: dict[str, int]
    notes: str = ""


@router.post("/sessions/{session_id:int}/marks", auth="staff")
def marks(ctx: Ctx):
    s, _ = _session(ctx)
    body = ctx.parse(MarksIn)
    try:
        return scoring.submit_faculty_marks(s["id"], ctx.user, body.dims, body.notes)
    except ValueError as exc:
        raise ApiError(422, str(exc), "invalid")


@router.post("/sessions/{session_id:int}/rescore", auth="staff", rate=(20, 3600))
def rescore(ctx: Ctx):
    s, _ = _session(ctx)
    tid = enqueue("rescore_session", {"session_id": s["id"]}, user_id=ctx.uid, dedupe_key=f"rescore:{s['id']}")
    audit(ctx.user, "rescore", "session", s["id"])
    return {"task_id": tid}


AUDIT_TEXT = {
    "student_join": "Student joined", "audio_played": "Recording played", "ai_score_viewed": "AI score viewed",
    "score_override": "Score overridden", "score_accepted": "AI score accepted", "faculty_marks": "Blind faculty marks entered",
    "rescore": "Re-scoring requested", "erase_recording": "Recording erased", "erase_everything": "Attempt erased",
    "export_csv": "Marks exported (CSV)", "sheets_link": "Google Sheets link created",
}


def audit_rows(entity: str, entity_id, limit: int = 300) -> list[dict]:
    rows = get_db().fetchall("SELECT * FROM audit_log WHERE entity = ? AND entity_id = ? ORDER BY id DESC LIMIT ?",
                             (entity, str(entity_id), limit))
    return [{"id": r["id"], "at": r["created_at"], "who": r["actor_name"], "action": r["action"],
             "what": AUDIT_TEXT.get(r["action"], r["action"].replace("_", " ")), "before": r.get("before"), "after": r.get("after"),
             "data": {k: v for k, v in (r.get("data") or {}).items() if k != "ip"}} for r in rows]


@router.get("/sessions/{session_id:int}/audit", auth="staff")
def session_audit(ctx: Ctx):
    """Who did what to this attempt: joins, playbacks, score views, overrides (with before/after and reason), erasures."""
    s, _ = _session(ctx)
    return {"entries": audit_rows("session", s["id"])}


class EraseIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    scope: str = Field(default="recording", pattern="^(recording|everything)$")


@router.post("/sessions/{session_id:int}/erase", auth="admin")
def erase(ctx: Ctx):
    """Right to erasure (DPDP Act 2023): delete the recording, or the whole attempt. Admin only, reason required,
    and the audit log keeps who erased what and why (never the erased content)."""
    import shutil

    from ..config import get_settings
    from ..db import now_iso
    from . import media

    s, _ = _session(ctx)
    body = ctx.parse(EraseIn)
    if s["status"] == "live":
        raise ApiError(409, "The viva is still running. Erase it after it ends.", "conflict")
    db = get_db()
    d = get_settings().sessions_dir / str(s["id"])
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    for k in [k for k in media._audio_cache if k.startswith(f"{s['id']}:")]:   # noqa: SLF001
        media._audio_cache.pop(k, None)                                           # noqa: SLF001
    if body.scope == "everything":
        for table in ("turns", "scores", "faculty_marks", "integrity_events"):
            db.execute(f"DELETE FROM {table} WHERE session_id = ?", (s["id"],))
        db.execute("DELETE FROM sessions WHERE id = ?", (s["id"],))
    else:
        db.update("sessions", s["id"], {"client": {**(s.get("client") or {}), "recording": {"erased_at": now_iso()}}})
    audit(ctx.user, f"erase_{body.scope}", "session", s["id"],
          before={"viva_id": s["viva_id"], "student_id": s["student_id"], "roll_no": s["roll_no"]}, data={"reason": body.reason})
    return {"ok": True, "scope": body.scope}


@router.get("/calibration", auth="staff")
def calibration(ctx: Ctx):
    vid = ctx.query.get("viva_id")
    return scoring.calibration(int(vid) if vid else None, None if ctx.is_admin else ctx.uid)


# ----------------------------------------------------------------------------- export
def _export_rows(v: dict) -> tuple[list[str], list[list]]:
    db = get_db()
    rows = db.fetchall("SELECT s.*, u.name AS student_name, u.roll_no FROM sessions s JOIN users u ON u.id = s.student_id "
                       "WHERE s.viva_id = ? ORDER BY u.roll_no", (v["id"],))
    rub = validate_rubric(v.get("rubric"))
    w = weights(rub)
    head = ["roll_no", "name", "status", "started_at", "final_total", "ai_total", "rule_total", "overridden_by", "override_reason",
            *[f"{k} (/5, w{w[k]:g})" for k in DIMENSIONS], "needs_review", "review_reasons", "integrity_warnings", "prompt_version",
            "feedback"]
    out = []
    for s in rows:
        sc = scoring.latest_scores(s["id"])
        final, ai, rule, ov = sc.get("final"), sc.get("ai"), sc.get("rule"), sc.get("override")
        dims = (final or {}).get("dims") or {}
        integ = integrity.summary(s["id"])
        out.append([s["roll_no"], s["student_name"], s["status"], s.get("started_at") or "", s.get("final_total") if s.get("final_total") is not None else "",
                    (ai or {}).get("total", ""), (rule or {}).get("total", ""), (ov or {}).get("reviewer_name", "") if ov and ov.get("is_final") else "",
                    (ov or {}).get("reason", "") if ov and ov.get("is_final") else "",
                    *[(dims.get(k) or {}).get("score", "") for k in DIMENSIONS], "yes" if s.get("needs_review") else "no",
                    "; ".join(r["text"] for r in ((ai or {}).get("review_reasons") or [])), integ["warn"],
                    (final or {}).get("prompt_version", ""), " | ".join(f["text"] for f in ((final or {}).get("feedback") or []))])
    return head, out


def _csv(head: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(head)
    wr.writerows(rows)
    return buf.getvalue()


@router.get("/vivas/{viva_id:int}/export.csv", auth="staff")
def export_csv(ctx: Ctx):
    v = _viva(ctx)
    head, rows = _export_rows(v)
    audit(ctx.user, "export_csv", "viva", v["id"], data={"rows": len(rows)})
    return Response(_csv(head, rows), media_type="text/csv",
                    headers={"content-disposition": f'attachment; filename="viva_{v["id"]}_results.csv"'})


@router.get("/vivas/{viva_id:int}/sheets-link", auth="staff")
def sheets_link(ctx: Ctx):
    """A private, expiring CSV link for Google Sheets: =IMPORTDATA("<link>") keeps a sheet in sync without OAuth."""
    v = _viva(ctx)
    from ..config import get_settings

    tok = signed_token(f"sheet:{v['id']}", 30 * 24 * 3600)
    url = f"{get_settings().base_url.rstrip('/')}/api/public/sheet/{tok}.csv"
    audit(ctx.user, "sheets_link", "viva", v["id"])
    return {"url": url, "formula": f'=IMPORTDATA("{url}")', "expires_days": 30}


@router.get("/public/sheet/{token}.csv", auth="public")
def sheet_csv(ctx: Ctx):
    data = verify_signed_token(ctx.path["token"])
    if not data or not data.startswith("sheet:"):
        raise ApiError(403, "Link expired or invalid", "forbidden")
    v = get_viva(int(data.split(":", 1)[1]))
    if not v:
        raise not_found("Viva")
    head, rows = _export_rows(v)
    return Response(_csv(head, rows), media_type="text/csv")


@router.get("/sessions/{session_id:int}/report.json", auth="staff")
def report_json(ctx: Ctx):
    detail = session_detail(ctx)
    return Response(json.dumps(detail, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"content-disposition": f'attachment; filename="session_{ctx.path["session_id"]}.json"'})
