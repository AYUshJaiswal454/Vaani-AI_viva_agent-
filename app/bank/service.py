"""Question banks in the database: import, edit, approve, version, export."""
from __future__ import annotations

import json
from pathlib import Path

from ..db import audit, get_db, now_iso
from .models import Question, assign_codes, bank_vocabulary, parse_upload, row_to_question, to_csv, validate_bank


def create_bank(owner: dict, name: str, subject: str, description: str = "", aliases: dict | None = None) -> int:
    return get_db().insert("question_banks", {
        "owner_id": owner["id"], "name": name.strip()[:120], "subject": subject.strip()[:120], "description": description[:2000],
        "aliases": aliases or {}, "version": 1, "validation": {}, "created_at": now_iso(), "updated_at": now_iso(),
    })


def get_bank(bank_id: int) -> dict | None:
    return get_db().fetchone("SELECT * FROM question_banks WHERE id = ?", (bank_id,))


def questions_of(bank_id: int, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM questions WHERE bank_id = ?"
    params: list = [bank_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = get_db().fetchall(sql + " ORDER BY phase, code", params)
    order = {"warmup": 0, "fundamentals": 1, "depth": 2, "applied": 3}
    return sorted((row_to_question(r) for r in rows), key=lambda q: (order.get(q["phase"], 9), q["code"]))


def add_questions(bank_id: int, questions: list[dict], *, source: str, status: str, actor: dict | None = None,
                  ai_meta: dict | None = None) -> tuple[int, int]:
    """Insert (or replace by code) validated questions. Returns (added, replaced)."""
    db = get_db()
    bank = get_bank(bank_id)
    existing = {r["code"]: r for r in db.fetchall("SELECT id, code FROM questions WHERE bank_id = ?", (bank_id,))}
    assign_codes(bank["subject"], questions, set(existing))
    added = replaced = 0
    now = now_iso()
    for q in questions:
        row = {
            "bank_id": bank_id, "code": q["code"], "topic": q["topic"], "phase": q["phase"], "qtype": q["type"],
            "difficulty": q["difficulty"], "text": q["question"], "rephrase": q.get("rephrase", ""),
            "key_points": q.get("key_points", []), "follow_ups": q.get("follow_ups", []), "hints": q.get("hints", []),
            "misconceptions": q.get("misconceptions", []), "code_snippet": q.get("code_snippet", ""),
            "expected_s": q.get("expected_seconds", 60), "tags": q.get("tags", []), "status": status, "source": source,
            "ai_meta": ai_meta or {}, "updated_at": now,
        }
        if status == "approved" and actor:
            row.update({"approved_by": actor["id"], "approved_at": now})
        if q["code"] in existing:
            db.update("questions", existing[q["code"]]["id"], row)
            replaced += 1
        else:
            db.insert("questions", {**row, "created_at": now})
            added += 1
    if status == "approved":
        bump_version(bank_id)
    refresh_validation(bank_id)
    return added, replaced


def import_upload(bank_id: int, filename: str, data: bytes, actor: dict, approve: bool = True) -> dict:
    parsed, errors = parse_upload(filename, data)
    qs = parsed.get("questions", [])
    if not qs:
        return {"added": 0, "replaced": 0, "errors": errors or ["No questions found in the file"]}
    added, replaced = add_questions(bank_id, qs, source="upload", status="approved" if approve else "draft", actor=actor)
    if parsed.get("aliases"):
        bank = get_bank(bank_id)
        get_db().update("question_banks", bank_id, {"aliases": {**(bank.get("aliases") or {}), **parsed["aliases"]}})
    audit(actor, "bank_import", "question_bank", bank_id, data={"file": filename, "added": added, "replaced": replaced,
                                                                 "errors": len(errors)})
    return {"added": added, "replaced": replaced, "errors": errors, "validation": refresh_validation(bank_id)}


def load_bank_file(owner: dict, path: Path, approve: bool = True) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    bank_id = create_bank(owner, raw["name"], raw["subject"], raw.get("description", ""), raw.get("aliases", {}))
    import_upload(bank_id, path.name, path.read_bytes(), owner, approve=approve)
    return bank_id


def update_question(question_id: int, data: dict, actor: dict) -> dict:
    db = get_db()
    row = db.fetchone("SELECT * FROM questions WHERE id = ?", (question_id,))
    if not row:
        raise KeyError("question")
    current = row_to_question(row)
    merged = {**current, **{k: v for k, v in data.items() if k in Question.model_fields}}
    q = Question.model_validate({k: merged[k] for k in Question.model_fields if k in merged}).model_dump()
    upd = {"topic": q["topic"], "phase": q["phase"], "qtype": q["type"], "difficulty": q["difficulty"], "text": q["question"],
           "rephrase": q["rephrase"], "key_points": q["key_points"], "follow_ups": q["follow_ups"], "hints": q["hints"],
           "misconceptions": q["misconceptions"], "code_snippet": q["code_snippet"], "expected_s": q["expected_seconds"],
           "tags": q["tags"], "updated_at": now_iso()}
    db.update("questions", question_id, upd)
    if row["status"] == "approved":
        bump_version(row["bank_id"])
    audit(actor, "question_edit", "question", question_id, before={"text": row["text"]}, after={"text": q["question"]})
    refresh_validation(row["bank_id"])
    return row_to_question(db.fetchone("SELECT * FROM questions WHERE id = ?", (question_id,)))


def set_status(question_ids: list[int], status: str, actor: dict) -> int:
    if status not in ("approved", "rejected", "draft"):
        raise ValueError("status")
    db = get_db()
    banks = set()
    n = 0
    for qid in question_ids:
        row = db.fetchone("SELECT id, bank_id, status FROM questions WHERE id = ?", (qid,))
        if not row:
            continue
        upd = {"status": status, "updated_at": now_iso()}
        if status == "approved":
            upd.update({"approved_by": actor["id"], "approved_at": now_iso()})
        db.update("questions", qid, upd)
        banks.add(row["bank_id"])
        n += 1
        audit(actor, f"question_{status}", "question", qid, before={"status": row["status"]}, after={"status": status})
    for b in banks:
        bump_version(b)
        refresh_validation(b)
    return n


def bump_version(bank_id: int) -> None:
    get_db().execute("UPDATE question_banks SET version = version + 1, updated_at = ? WHERE id = ?", (now_iso(), bank_id))


def refresh_validation(bank_id: int) -> dict:
    bank = get_bank(bank_id)
    rep = validate_bank(questions_of(bank_id), bank.get("aliases"))
    get_db().update("question_banks", bank_id, {"validation": rep})
    return rep


def export_bank(bank_id: int, fmt: str = "json") -> tuple[str, str]:
    bank = get_bank(bank_id)
    qs = questions_of(bank_id)
    if fmt == "csv":
        return to_csv(qs), "text/csv"
    body = {"name": bank["name"], "subject": bank["subject"], "description": bank.get("description", ""),
            "aliases": bank.get("aliases") or {}, "version": bank["version"],
            "questions": [{k: q[k] for k in ("code", "topic", "phase", "type", "difficulty", "question", "rephrase", "key_points",
                                             "follow_ups", "hints", "misconceptions", "code_snippet", "expected_seconds", "tags",
                                             "status", "source")} for q in qs]}
    return json.dumps(body, indent=1, ensure_ascii=False), "application/json"


def engine_bank(bank_id: int) -> tuple[dict[str, dict], dict, set]:
    """Approved questions keyed by code, aliases and vocabulary, as the examiner needs them."""
    bank = get_bank(bank_id)
    qs = questions_of(bank_id, "approved")
    aliases = bank.get("aliases") or {}
    return {q["code"]: q for q in qs}, aliases, bank_vocabulary(qs, aliases)
