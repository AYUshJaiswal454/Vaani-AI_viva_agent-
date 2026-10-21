"""Sample vivas: export finished sessions (audio + transcript + scores) to plain files, and load them back.

The repository ships five recorded sample vivas in samples/vivas/. On first start (SEED_DEMO=true, empty database)
they are imported into the demo viva so the faculty dashboard has real recordings to open straight away. Audio is
stored unencrypted in the repository (synthetic voices, no real student) and re-encrypted with this server's key
on import, exactly like a live recording.

    python scripts/export_samples.py --data-dir <DATA_DIR> --results loadtest/results/<run>.json
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .config import ROOT, get_settings
from .db import get_db, now_iso
from .voice.audio import read_secure, write_secure

log = logging.getLogger("vaani.samples")
SAMPLES_DIR = ROOT / "samples" / "vivas"
SESSION_COLS = ("status", "consent", "plan", "state", "bank_version", "engine_version", "settings", "started_at", "ended_at",
                "end_reason", "started_epoch", "paused_s", "audio_samples", "flags", "latency", "client", "final_total",
                "needs_review", "created_at", "updated_at")
TURN_COLS = ("seq", "speaker", "kind", "phase", "text", "source", "question_code", "t_start", "t_end", "meta", "created_at")
SCORE_COLS = ("kind", "dims", "total", "feedback", "prompt_version", "model", "runs", "confidence", "needs_review",
              "review_reasons", "reviewer_name", "reason", "is_final", "created_at")
EVENT_COLS = ("t", "kind", "severity", "detail", "created_at")
FILES = ("conversation.ogg", "student.ogg")


def _fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def transcript_markdown(student: dict, viva: dict, turns: list[dict], scores: list[dict]) -> str:
    final = next((s for s in scores if s["is_final"]), scores[-1] if scores else None)
    out = [f"# {viva['title']}: {student['name']} ({student['roll_no']})", ""]
    if final:
        out.append(f"Total: **{final['total']:.0f} / 100** ({final['kind']} score, confidence {final.get('confidence') or '-'})")
        out.append("")
    for t in turns:
        who = "Examiner" if t["speaker"] == "examiner" else ("Student" if t["speaker"] == "student" else "System")
        src = f" `{t['source']}`" if t.get("source") else ""
        out.append(f"- **[{_fmt(t['t_start'])}] {who}**{src}: {t['text']}")
    if final:
        out += ["", "## Scores", "", "| Dimension | Score | Reason |", "|---|---|---|"]
        for k, d in final["dims"].items():
            out.append(f"| {k} | {d['score']} / 5 | {d.get('reason', '').replace('|', '/')} |")
        if final.get("feedback"):
            out += ["", "## Feedback", ""] + [f"- {f['text'] if isinstance(f, dict) else f}" for f in final["feedback"]]
    return "\n".join(out) + "\n"


def export_session(session_id: int, dest: Path, label: str = "") -> dict:
    db = get_db()
    sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    student = db.fetchone("SELECT name, roll_no FROM users WHERE id = ?", (sess["student_id"],))
    viva = db.fetchone("SELECT access_code, title FROM vivas WHERE id = ?", (sess["viva_id"],))
    turns = db.fetchall("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (session_id,))
    scores = db.fetchall("SELECT * FROM scores WHERE session_id = ? ORDER BY id", (session_id,))
    events = db.fetchall("SELECT * FROM integrity_events WHERE session_id = ? ORDER BY id", (session_id,))
    dest.mkdir(parents=True, exist_ok=True)
    src = get_settings().sessions_dir / str(session_id)
    for name in FILES:
        if (src / name).exists():
            (dest / name).write_bytes(read_secure(src / name))
    if (src / "waveform.json").exists():
        shutil.copy(src / "waveform.json", dest / "waveform.json")
    doc = {"label": label, "viva_code": viva["access_code"], "student": dict(student),
           "session": {k: sess[k] for k in SESSION_COLS}, "turns": [{k: t[k] for k in TURN_COLS} for t in turns],
           "scores": [{k: s[k] for k in SCORE_COLS} for s in scores], "integrity_events": [{k: e[k] for k in EVENT_COLS} for e in events]}
    (dest / "session.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    (dest / "transcript.md").write_text(transcript_markdown(dict(student), dict(viva), turns, scores), encoding="utf-8")
    final = next((s for s in scores if s["is_final"]), scores[-1] if scores else None)
    return {"dir": dest.name, "label": label, "student": student["name"], "roll_no": student["roll_no"],
            "total": final["total"] if final else None, "needs_review": bool(sess["needs_review"]),
            "duration_s": (sess.get("client") or {}).get("recording", {}).get("duration_s"), "turns": len(turns)}


def import_samples(directory: Path = SAMPLES_DIR) -> int:
    """Load exported sample vivas into this database (skips ones already present). Returns how many were added."""
    if not directory.exists():
        return 0
    db = get_db()
    added = 0
    for d in sorted(p for p in directory.iterdir() if (p / "session.json").exists()):
        doc = json.loads((d / "session.json").read_text(encoding="utf-8"))
        viva = db.fetchone("SELECT * FROM vivas WHERE access_code = ?", (doc["viva_code"],))
        if not viva:
            continue
        st = doc["student"]
        user = db.fetchone("SELECT * FROM users WHERE roll_no = ?", (st["roll_no"],))
        if user and db.fetchone("SELECT id FROM sessions WHERE viva_id = ? AND student_id = ?", (viva["id"], user["id"])):
            continue
        if not user:
            uid = db.insert("users", {"role": "student", "name": st["name"], "roll_no": st["roll_no"], "settings": {"sample": True},
                                      "created_at": now_iso()})
        else:
            uid = user["id"]
        sid = db.insert("sessions", {**doc["session"], "viva_id": viva["id"], "student_id": uid, "conn_token": None,
                                     "flags": {**(doc["session"].get("flags") or {}), "sample": doc.get("label") or True}})
        for t in doc["turns"]:
            db.insert("turns", {**t, "session_id": sid})
        for s in doc["scores"]:
            db.insert("scores", {**s, "session_id": sid, "reviewer_id": None})
        for e in doc["integrity_events"]:
            db.insert("integrity_events", {**e, "session_id": sid})
        out = get_settings().sessions_dir / str(sid)
        out.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            if (d / name).exists():
                write_secure(out / name, (d / name).read_bytes())
        if (d / "waveform.json").exists():
            shutil.copy(d / "waveform.json", out / "waveform.json")
        added += 1
    if added:
        log.info("Imported %d sample vivas", added)
    return added
