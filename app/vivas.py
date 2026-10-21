"""Vivas (exam configurations) and sessions (one student's attempt)."""
from __future__ import annotations

import secrets
from typing import Any

from .bank.models import bank_vocabulary
from .bank.service import engine_bank, get_bank, questions_of, refresh_validation
from .config import get_settings
from .db import audit, get_db, now_iso, parse_iso, utcnow
from .engine.examiner import ENGINE_VERSION, make_plan, validate_phase_plan
from .scoring.rubric import validate_rubric

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O/1/I confusion when read aloud in class
CONSENT_VERSION = "consent-2026.1"
CONSENT_TEXT = (
    "This viva is conducted by an AI examiner. Your voice will be recorded, converted to text, and stored with the "
    "transcript, the questions asked and the scoring. Your faculty can listen to the recording, read the transcript "
    "and change any mark; the AI's score is only a recommendation until faculty review. Signals such as switching "
    "tabs or long silences are noted for your faculty but never change your marks automatically. Recordings are "
    "encrypted and kept for {days} days, then deleted. If you do not consent, close this page and speak to your faculty "
    "about an in-person viva."
)

DEFAULT_SETTINGS = {
    "stt_engine": None,            # None = server default (see config.default_stt_engine)
    "tts_engine": None,
    "language": "en",              # en | hinglish  (hinglish: answers in Hindi-English are fully accepted)
    "live_llm": True,              # LLM-phrased follow-ups that refer to what the student said (when a provider is set)
    "integrity": True,             # tab switches, long silences, reading and second-voice signals
    "code_round": True,            # show code for applied problems and a scratchpad the student can type in
    "allow_end_early": True,
    "barge_in": True,              # the student may start answering before the examiner finishes speaking
    "attempts": 1,
}


def new_access_code() -> str:
    db = get_db()
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not db.fetchone("SELECT id FROM vivas WHERE access_code = ?", (code,)):
            return code


def viva_settings(viva: dict) -> dict:
    s = get_settings()
    merged = {**DEFAULT_SETTINGS, **(viva.get("settings") or {})}
    merged["stt_engine"] = merged.get("stt_engine") or s.stt_engine
    merged["tts_engine"] = merged.get("tts_engine") or s.tts_engine
    return merged


def create_viva(owner: dict, data: dict) -> dict:
    bank = get_bank(int(data["bank_id"]))
    if not bank:
        raise ValueError("Question bank not found")
    phases = data.get("phase_plan") or {}
    plan = validate_phase_plan(phases.get("phases"), int(data.get("duration_s") or 900)) if phases.get("phases") else None
    if plan and not (phases.get("justification") or "").strip():
        raise ValueError("A customised phase plan needs a written justification")
    rubric = validate_rubric(data.get("rubric"))
    settings = {k: v for k, v in (data.get("settings") or {}).items() if k in DEFAULT_SETTINGS}
    now = now_iso()
    vid = get_db().insert("vivas", {
        "owner_id": owner["id"], "bank_id": bank["id"], "title": (data.get("title") or bank["subject"] + " viva").strip()[:160],
        "subject": (data.get("subject") or bank["subject"]).strip()[:120], "topics": data.get("topics") or [],
        "difficulty": data.get("difficulty") or "mixed", "duration_s": 900, "access_code": new_access_code(),
        "status": "draft", "settings": settings,
        "phase_plan": {"phases": plan, "justification": phases.get("justification", "")} if plan else {},
        "rubric": rubric, "roster": clean_roster(data.get("roster") or []), "opens_at": data.get("opens_at"),
        "closes_at": data.get("closes_at"), "created_at": now, "updated_at": now,
    })
    audit(owner, "viva_create", "viva", vid, after={"title": data.get("title"), "bank": bank["id"]})
    return get_viva(vid)


def clean_roster(roster: list[Any]) -> list[dict]:
    out, seen = [], set()
    for r in roster:
        if isinstance(r, str):
            parts = [p.strip() for p in r.replace("\t", ",").split(",")]
            r = {"roll_no": parts[0], "name": parts[1] if len(parts) > 1 else ""}
        roll = str(r.get("roll_no") or "").strip().upper()
        if roll and roll not in seen:
            seen.add(roll)
            out.append({"roll_no": roll, "name": str(r.get("name") or "").strip()[:80]})
    return out


def get_viva(viva_id: int) -> dict | None:
    return get_db().fetchone("SELECT * FROM vivas WHERE id = ?", (viva_id,))


def update_viva(viva: dict, data: dict, actor: dict) -> dict:
    upd: dict[str, Any] = {}
    for k in ("title", "subject", "topics", "difficulty", "opens_at", "closes_at"):
        if k in data:
            upd[k] = data[k]
    if "settings" in data:
        upd["settings"] = {**(viva.get("settings") or {}), **{k: v for k, v in data["settings"].items() if k in DEFAULT_SETTINGS}}
    if "roster" in data:
        upd["roster"] = clean_roster(data["roster"])
    if "rubric" in data:
        upd["rubric"] = validate_rubric(data["rubric"])
    if "phase_plan" in data:
        pp = data["phase_plan"] or {}
        if pp.get("phases"):
            if not (pp.get("justification") or "").strip():
                raise ValueError("A customised phase plan needs a written justification")
            upd["phase_plan"] = {"phases": validate_phase_plan(pp["phases"]), "justification": pp["justification"]}
        else:
            upd["phase_plan"] = {}
    if "bank_id" in data and int(data["bank_id"]) != viva["bank_id"]:
        if viva["status"] == "open":
            raise ValueError("Close the viva before changing its question bank")
        upd["bank_id"] = int(data["bank_id"])
    if upd:
        upd["updated_at"] = now_iso()
        get_db().update("vivas", viva["id"], upd)
        audit(actor, "viva_update", "viva", viva["id"], before={k: viva.get(k) for k in upd if k != "updated_at"},
              after={k: v for k, v in upd.items() if k != "updated_at"})
    return get_viva(viva["id"])


def set_status(viva: dict, status: str, actor: dict) -> dict:
    if status == "open":
        rep = refresh_validation(viva["bank_id"])
        if not rep["ready"]:
            raise ValueError("The question bank is not ready: " + "; ".join(rep["errors"]))
    get_db().update("vivas", viva["id"], {"status": status, "updated_at": now_iso()})
    audit(actor, f"viva_{status}", "viva", viva["id"])
    if status == "open":
        from .tasks import enqueue

        enqueue("presynth_viva", {"viva_id": viva["id"]}, dedupe_key=f"presynth:{viva['id']}")
    return get_viva(viva["id"])


def viva_is_open(viva: dict) -> tuple[bool, str]:
    if viva["status"] != "open":
        return False, "This viva is not open right now. Please check with your faculty."
    now = utcnow()
    o, c = parse_iso(viva.get("opens_at")), parse_iso(viva.get("closes_at"))
    if o and now < o:
        return False, f"This viva opens at {viva['opens_at']}."
    if c and now > c:
        return False, "This viva has closed."
    return True, ""


# ----------------------------------------------------------------------------- students and sessions
def find_or_create_student(roll_no: str, name: str) -> dict:
    db = get_db()
    roll = roll_no.strip().upper()
    row = db.fetchone("SELECT * FROM users WHERE roll_no = ?", (roll,))
    if row:
        if name and name.strip() and row["name"] != name.strip():
            db.update("users", row["id"], {"name": name.strip()[:80]})
            row = db.fetchone("SELECT * FROM users WHERE id = ?", (row["id"],))
        return row
    uid = db.insert("users", {"role": "student", "name": (name or roll).strip()[:80], "roll_no": roll, "settings": {},
                              "created_at": now_iso()})
    return db.fetchone("SELECT * FROM users WHERE id = ?", (uid,))


def join(access_code: str, roll_no: str, name: str) -> tuple[dict, dict]:
    db = get_db()
    viva = db.fetchone("SELECT * FROM vivas WHERE access_code = ?", (access_code.strip().upper(),))
    if not viva:
        raise PermissionError("No viva found for that code. Check the code your faculty gave you.")
    ok, msg = viva_is_open(viva)
    if not ok:
        raise PermissionError(msg)
    roll = roll_no.strip().upper()
    roster = viva.get("roster") or []
    if roster:
        entry = next((r for r in roster if r["roll_no"] == roll), None)
        if not entry:
            raise PermissionError("Your roll number is not on this viva's list. Please speak to your faculty.")
        name = name or entry.get("name") or ""
    if not roll or len(roll) < 3:
        raise ValueError("Please enter your roll number")
    return find_or_create_student(roll, name), viva


def current_session(viva: dict, student: dict) -> dict | None:
    return get_db().fetchone("SELECT * FROM sessions WHERE viva_id = ? AND student_id = ? ORDER BY id DESC LIMIT 1",
                             (viva["id"], student["id"]))


def start_or_resume(viva: dict, student: dict) -> dict:
    """One attempt per student by default. An unfinished attempt is resumed (rejoin after a drop)."""
    sess = current_session(viva, student)
    settings = viva_settings(viva)
    if sess and sess["status"] in ("created", "consented", "live"):
        return sess
    if sess and sess["status"] not in ("abandoned",):
        done = get_db().scalar("SELECT COUNT(*) FROM sessions WHERE viva_id = ? AND student_id = ? AND status != 'abandoned'",
                               (viva["id"], student["id"]))
        if done >= int(settings.get("attempts") or 1):
            raise PermissionError("You have already completed this viva.")
    return create_session(viva, student)


def create_session(viva: dict, student: dict) -> dict:
    qmap, aliases, _ = engine_bank(viva["bank_id"])
    bank = get_bank(viva["bank_id"])
    plan = make_plan(list(qmap.values()), f"viva-{viva['id']}:{student['roll_no']}", viva.get("difficulty") or "mixed",
                     viva.get("topics") or None)
    used = {plan["opener"], *plan["fundamentals"], *plan["applied"], *[c for cs in plan["depth"].values() for c in cs]}
    plan["snapshot"] = {c: qmap[c] for c in used if c and c in qmap}     # exact question text used, even if edited later
    plan["aliases"] = aliases
    now = now_iso()
    settings = viva_settings(viva)
    sid = get_db().insert("sessions", {
        "viva_id": viva["id"], "student_id": student["id"], "status": "created", "plan": plan, "state": {},
        "bank_version": bank["version"], "engine_version": ENGINE_VERSION,
        "settings": {"stt_engine": settings["stt_engine"], "tts_engine": settings["tts_engine"], "language": settings["language"],
                     "live_llm": settings["live_llm"], "code_round": settings["code_round"], "integrity": settings["integrity"],
                     "barge_in": settings["barge_in"], "allow_end_early": settings["allow_end_early"],
                     "phases": (viva.get("phase_plan") or {}).get("phases"), "whisper_model": get_settings().whisper_model},
        "flags": {}, "latency": {}, "client": {}, "created_at": now, "updated_at": now,
    })
    return get_session(sid)


def get_session(session_id: int) -> dict | None:
    return get_db().fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))


def record_consent(sess: dict, accepted: bool, client: dict) -> dict:
    if not accepted:
        raise ValueError("Consent is required to take the AI viva")
    consent = {"accepted_at": now_iso(), "version": CONSENT_VERSION, "ip": client.get("ip"), "ua": (client.get("ua") or "")[:200]}
    get_db().update("sessions", sess["id"], {"consent": consent, "status": "consented" if sess["status"] == "created" else sess["status"],
                                             "client": {**(sess.get("client") or {}), **client}, "updated_at": now_iso()})
    return get_session(sess["id"])


def consent_text() -> str:
    s = get_settings()
    text = CONSENT_TEXT.format(days=s.retention_days)
    if s.consent_contact:
        text += f" Questions, corrections or erasure requests about your data: {s.consent_contact}."
    return text


def examiner_context(sess: dict, viva: dict, student: dict) -> tuple[dict, dict]:
    """(questions by code, context) for the examiner, built only from the session's own snapshot."""
    plan = sess["plan"]
    qmap = plan.get("snapshot") or {}
    aliases = plan.get("aliases") or {}
    ctx = {"student_name": student["name"], "roll_no": student["roll_no"], "subject": viva["subject"], "plan": plan,
           "vocab": bank_vocabulary(list(qmap.values()), aliases), "aliases": aliases,
           "phases": (sess.get("settings") or {}).get("phases")}
    return qmap, ctx


def question_prompt(q: dict, limit: int = 6) -> str:
    """A short local-Whisper prompt for one question: the question itself (Whisper's prompt works best as natural
    text, and the question already spells the subject's terms correctly) plus the main term of each key point."""
    terms: list[str] = []
    for kp in q.get("key_points", []) or []:
        t = (kp.get("terms") or [""])[0] if isinstance(kp, dict) else ""
        if 3 <= len(t) <= 30 and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
    text = (q.get("question") or q.get("text") or "").strip()
    return (text[:200] + (" " + ", ".join(terms[:limit]) if terms else "")).strip()


def subject_prompt(sess: dict, limit: int = 60) -> str:
    """Key technical terms for the speech recogniser (Whisper prompt / Sarvam keyterms)."""
    terms: list[str] = []
    for q in (sess["plan"].get("snapshot") or {}).values():
        for kp in q.get("key_points", []):
            for t in kp.get("terms", [])[:2]:
                if 3 <= len(t) <= 30 and t.lower() not in {x.lower() for x in terms}:
                    terms.append(t)
    return ", ".join(terms[:limit])
