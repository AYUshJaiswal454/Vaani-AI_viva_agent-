"""Scoring a finished viva, deciding when faculty review is mandatory, overrides and calibration."""
from __future__ import annotations

import math
from statistics import mean, median

from ..config import get_settings
from ..db import audit, get_db, now_iso
from .llm_scorer import llm_score, prompt_version
from .rubric import DIMENSIONS, total_100, validate_rubric
from .rule_scorer import RULE_VERSION, rule_score
from .transcript import SUBSTANTIVE, answer_of

DISAGREE_TOTAL = 15          # AI vs rule scorer: total difference that forces review
DISAGREE_DIM = 2             # ... or any single dimension this far apart
LOW_STT_CONF = 0.55


def load_turns(session_id: int) -> list[dict]:
    return get_db().fetchall("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (session_id,))


def review_reasons(turns: list[dict], rule: dict, ai: dict | None, integrity: list[dict], sess: dict) -> tuple[list[dict], str]:
    """Reasons a person must look at this viva before marks are released. Returns (reasons, confidence)."""
    reasons: list[dict] = []

    def add(code: str, text: str, mandatory: bool = True):
        reasons.append({"code": code, "text": text, "mandatory": mandatory})

    if ai and not ai.get("error"):
        for k in DIMENSIONS:
            sp = ai["dims"][k].get("spread", 0)
            if sp >= 2:
                add("ai_unstable", f"AI runs disagree on {k.replace('_', ' ')} by {sp} points")
        if abs(ai["total"] - rule["total"]) >= DISAGREE_TOTAL:
            add("scorers_disagree", f"AI ({ai['total']:g}) and rule-based ({rule['total']:g}) totals differ by "
                                    f"{abs(ai['total'] - rule['total']):.0f}")
        else:
            for k in DIMENSIONS:
                d = abs(ai["dims"][k]["score"] - rule["dims"][k]["score"])
                if d >= DISAGREE_DIM:
                    add("scorers_disagree", f"AI and rule-based scorers disagree on {k.replace('_', ' ')} "
                                            f"({ai['dims'][k]['score']} vs {rule['dims'][k]['score']})")
                    break
        if any("without a valid student-line citation" in p for p in ai.get("problems", [])):
            add("uncited", "Some AI marks were not backed by a transcript citation")
        if ai.get("model_confidence") == "low":
            add("ai_low_confidence", "The AI scorer reported low confidence")
        if any(p.startswith("model concern") for p in ai.get("problems", [])):
            add("ai_concern", "The AI scorer raised a concern: " + next(p for p in ai["problems"] if p.startswith("model concern"))[15:120],
                mandatory=False)
    elif ai and ai.get("error"):
        add("ai_failed", "AI scorer failed; marks come from the rule-based scorer only", mandatory=False)
    student = [t for t in turns if t["speaker"] == "student"]
    confs = [((t.get("meta") or {}).get("stt") or {}).get("confidence") for t in student]
    confs = [c for c in confs if isinstance(c, (int, float))]
    if confs and (mean(confs) < LOW_STT_CONF or sum(1 for c in confs if c < 0.4) / len(confs) > 0.25):
        add("stt_low", f"Speech recognition confidence is low (average {mean(confs):.2f}); check the audio")
    substantive = [t for t in student if answer_of(t).get("label") in SUBSTANTIVE]
    if len(substantive) < 4:
        add("little_evidence", f"Only {len(substantive)} answers to score")
    warn = [e for e in integrity if e["severity"] == "warn"]
    kinds = {}
    for e in integrity:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    if kinds.get("tab_hidden", 0) >= 3:
        add("integrity_tabs", f"Student left the viva tab {kinds['tab_hidden']} times")
    for k, label in (("identity_mismatch", "Spoken roll number did not match the login"),
                     ("reading_suspected", "Answers looked read aloud"), ("second_voice", "A second voice may have been present"),
                     ("injection", "Student tried to give instructions to the AI")):
        if kinds.get(k):
            add(f"integrity_{k}", f"{label} ({kinds[k]}x)")
    if sess.get("end_reason") in ("disconnected",):
        add("disconnected", "The viva ended because the student disconnected")
    if (sess.get("paused_s") or 0) > 30:
        add("network", f"Network problems paused the clock for {sess['paused_s']:.0f} s", mandatory=False)
    if "applied problem not reached" in rule.get("notes", []):
        add("no_applied", "The applied problem was not reached; problem solving has no evidence", mandatory=False)
    _ = warn
    mandatory = any(r["mandatory"] for r in reasons)
    conf = "low" if mandatory else ("medium" if reasons or not ai or ai.get("error") else "high")
    return reasons, conf


def redact_identity(turns: list[dict], student: dict) -> list[dict]:
    """Remove the student's name and roll number before the transcript goes to an AI scorer: the scorer does not
    need them (fairness) and a hosted provider should not receive them (privacy)."""
    import re

    pats = [re.escape(p) for p in (student.get("name") or "").split() if len(p) >= 3]
    if student.get("roll_no"):
        roll = re.sub(r"[^A-Za-z0-9]", "", student["roll_no"])
        pats.append(r"[\s-]*".join(re.escape(c) for c in roll))
    if not pats:
        return turns
    rx = re.compile(r"\b(" + "|".join(pats) + r")\b", re.I)
    return [{**t, "text": rx.sub("[redacted]", t["text"] or "")} for t in turns]


async def score_session(session_id: int) -> dict:
    db = get_db()
    sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    viva = db.fetchone("SELECT * FROM vivas WHERE id = ?", (sess["viva_id"],))
    rubric = validate_rubric(viva.get("rubric"))
    questions = (sess.get("plan") or {}).get("snapshot") or {}
    turns = load_turns(session_id)
    integrity = db.fetchall("SELECT * FROM integrity_events WHERE session_id = ?", (session_id,))
    db.update("sessions", session_id, {"status": "scoring", "updated_at": now_iso()})
    rule = rule_score(turns, questions, rubric)
    student = db.fetchone("SELECT name, roll_no FROM users WHERE id = ?", (sess["student_id"],)) or {}
    ai = await llm_score(redact_identity(turns, student), questions, rubric, viva["subject"], session_id,
                         runs=get_settings().scorer_runs)
    reasons, conf = review_reasons(turns, rule, ai, integrity, sess)
    now = now_iso()
    db.execute("UPDATE scores SET is_final = 0 WHERE session_id = ? AND kind IN ('ai', 'rule')", (session_id,))
    db.insert("scores", {"session_id": session_id, "kind": "rule", "dims": rule["dims"], "total": rule["total"],
                         "feedback": rule["feedback"], "prompt_version": RULE_VERSION, "model": "rule-scorer",
                         "runs": [], "confidence": None, "needs_review": 0, "review_reasons": [], "is_final": 0, "created_at": now})
    if ai and not ai.get("error"):
        primary = {"dims": ai["dims"], "total": ai["total"], "feedback": ai["feedback"] or rule["feedback"],
                   "prompt_version": ai["prompt_version"], "model": ai["model"], "runs": ai["runs"]}
    else:
        primary = {"dims": rule["dims"], "total": rule["total"], "feedback": rule["feedback"],
                   "prompt_version": f"{RULE_VERSION}|{rubric.get('version')}", "model": "rule-scorer",
                   "runs": [{"error": ai.get("error")}] if ai else []}
    has_override = db.fetchone("SELECT id FROM scores WHERE session_id = ? AND kind = 'override' AND is_final = 1", (session_id,))
    needs = any(r["mandatory"] for r in reasons)
    sid = db.insert("scores", {"session_id": session_id, "kind": "ai", **primary, "confidence": conf, "needs_review": int(needs),
                               "review_reasons": reasons, "is_final": 0 if has_override else 1, "created_at": now})
    upd = {"status": "reviewed" if has_override else "scored", "needs_review": 0 if has_override else int(needs),
           "updated_at": now_iso()}
    if not has_override:
        upd["final_total"] = primary["total"]
    db.update("sessions", session_id, upd)
    return {"score_id": sid, "total": primary["total"], "rule_total": rule["total"], "ai_total": ai.get("total") if ai else None,
            "needs_review": needs, "reasons": reasons, "confidence": conf}


def latest_scores(session_id: int) -> dict:
    rows = get_db().fetchall("SELECT * FROM scores WHERE session_id = ? ORDER BY id", (session_id,))
    out: dict = {"ai": None, "rule": None, "override": None, "final": None, "history": []}
    for r in rows:
        out[r["kind"]] = r
        out["history"].append({k: r[k] for k in ("id", "kind", "total", "reviewer_name", "reason", "created_at", "is_final")})
        if r["is_final"]:
            out["final"] = r
    return out


def override(session_id: int, actor: dict, dims: dict[str, int], reason: str) -> dict:
    """Faculty override: always wins, logged with the reviewer's name and reason."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Please give a reason of at least 10 characters for the change")
    db = get_db()
    sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    viva = db.fetchone("SELECT * FROM vivas WHERE id = ?", (sess["viva_id"],))
    rubric = validate_rubric(viva.get("rubric"))
    cur = latest_scores(session_id)
    base = cur["final"] or cur["ai"]
    if not base:
        raise ValueError("This viva has not been scored yet")
    new_dims = {}
    for k in DIMENSIONS:
        old = base["dims"][k]
        if k in dims and dims[k] is not None:
            v = int(dims[k])
            if not 0 <= v <= 5:
                raise ValueError(f"{k}: score must be between 0 and 5")
        else:
            v = old["score"]
        new_dims[k] = {"score": v, "reason": old.get("reason", "") if v == old["score"] else f"Changed by {actor['name']}: {reason}",
                       "evidence": old.get("evidence", []), "ai_score": (cur["ai"] or base)["dims"][k]["score"]}
    total = total_100({k: new_dims[k]["score"] for k in DIMENSIONS}, rubric)
    db.execute("UPDATE scores SET is_final = 0 WHERE session_id = ?", (session_id,))
    sid = db.insert("scores", {"session_id": session_id, "kind": "override", "dims": new_dims, "total": total,
                               "feedback": base.get("feedback") or [], "prompt_version": base.get("prompt_version"),
                               "model": "faculty", "runs": [], "confidence": "high", "needs_review": 0, "review_reasons": [],
                               "reviewer_id": actor["id"], "reviewer_name": actor["name"], "reason": reason, "is_final": 1,
                               "created_at": now_iso()})
    db.update("sessions", session_id, {"final_total": total, "needs_review": 0, "status": "reviewed", "updated_at": now_iso()})
    audit(actor, "score_override", "session", session_id,
          before={"total": base["total"], "dims": {k: base["dims"][k]["score"] for k in DIMENSIONS}},
          after={"total": total, "dims": {k: new_dims[k]["score"] for k in DIMENSIONS}}, data={"reason": reason})
    return db.fetchone("SELECT * FROM scores WHERE id = ?", (sid,))


def accept_ai(session_id: int, actor: dict, note: str = "") -> None:
    """Faculty reviewed and accepts the AI score as it is (clears the review flag, logged)."""
    db = get_db()
    db.update("sessions", session_id, {"needs_review": 0, "status": "reviewed", "updated_at": now_iso()})
    audit(actor, "score_accepted", "session", session_id, data={"note": note[:300]})


# ----------------------------------------------------------------------------- calibration
def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    return pearson(ranks(xs), ranks(ys)) if len(xs) >= 3 else None


def calibration(viva_id: int | None = None, owner_id: int | None = None) -> dict:
    """AI score vs faculty marks entered blind: mean absolute gap, correlation, per-dimension bias."""
    db = get_db()
    sql = ("SELECT fm.*, s.viva_id, u.name AS student, u.roll_no FROM faculty_marks fm JOIN sessions s ON s.id = fm.session_id "
           "JOIN users u ON u.id = s.student_id JOIN vivas v ON v.id = s.viva_id WHERE 1=1")
    params: list = []
    if viva_id:
        sql += " AND s.viva_id = ?"
        params.append(viva_id)
    if owner_id:
        sql += " AND v.owner_id = ?"
        params.append(owner_id)
    rows = db.fetchall(sql, params)
    pairs = []
    for r in rows:
        ai = db.fetchone("SELECT * FROM scores WHERE session_id = ? AND kind = 'ai' ORDER BY id DESC LIMIT 1", (r["session_id"],))
        if not ai:
            continue
        pairs.append({"session_id": r["session_id"], "student": r["student"], "roll_no": r["roll_no"], "faculty_total": r["total"],
                      "ai_total": ai["total"], "faculty_dims": r["dims"], "ai_dims": {k: ai["dims"][k]["score"] for k in DIMENSIONS},
                      "gap": round(ai["total"] - r["total"], 1), "blind": bool(r["blind"])})
    if not pairs:
        return {"n": 0, "pairs": []}
    f = [p["faculty_total"] for p in pairs]
    a = [p["ai_total"] for p in pairs]
    per_dim = {}
    for k in DIMENSIONS:
        fd = [float(p["faculty_dims"].get(k, 0)) for p in pairs]
        ad = [float(p["ai_dims"][k]) for p in pairs]
        per_dim[k] = {"mae": round(mean(abs(x - y) for x, y in zip(ad, fd)), 2), "bias": round(mean(x - y for x, y in zip(ad, fd)), 2),
                      "exact": round(sum(1 for x, y in zip(ad, fd) if x == y) / len(fd), 2),
                      "within_1": round(sum(1 for x, y in zip(ad, fd) if abs(x - y) <= 1) / len(fd), 2)}
    gaps = [abs(x - y) for x, y in zip(a, f)]
    r = pearson(a, f)
    rho = spearman(a, f)
    return {"n": len(pairs), "mae_total": round(mean(gaps), 2), "median_gap": round(median(gaps), 2),
            "bias_total": round(mean(x - y for x, y in zip(a, f)), 2), "within_10": round(sum(1 for g in gaps if g <= 10) / len(gaps), 2),
            "pearson": None if r is None else round(r, 3), "spearman": None if rho is None else round(rho, 3),
            "per_dimension": per_dim, "pairs": pairs}


def submit_faculty_marks(session_id: int, actor: dict, dims: dict[str, int], notes: str = "") -> dict:
    db = get_db()
    sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    viva = db.fetchone("SELECT * FROM vivas WHERE id = ?", (sess["viva_id"],))
    rubric = validate_rubric(viva.get("rubric"))
    clean = {}
    for k in DIMENSIONS:
        v = int(dims.get(k, 0))
        if not 0 <= v <= 5:
            raise ValueError(f"{k}: 0-5")
        clean[k] = v
    total = total_100(clean, rubric)
    seen_ai = db.fetchone("SELECT id FROM audit_log WHERE action = 'ai_score_viewed' AND actor_id = ? AND entity_id = ?",
                          (actor["id"], str(session_id))) is not None
    db.execute("DELETE FROM faculty_marks WHERE session_id = ? AND faculty_id = ?", (session_id, actor["id"]))
    db.insert("faculty_marks", {"session_id": session_id, "faculty_id": actor["id"], "dims": clean, "total": total,
                                "notes": notes[:1000], "blind": int(not seen_ai), "created_at": now_iso()})
    audit(actor, "faculty_marks", "session", session_id, after={"total": total, "dims": clean, "blind": not seen_ai})
    return {"total": total, "blind": not seen_ai}


__all__ = ["score_session", "override", "accept_ai", "calibration", "latest_scores", "submit_faculty_marks", "prompt_version"]
