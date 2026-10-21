"""Faculty: question banks (upload CSV/JSON, AI drafts from a syllabus, edit, approve, export)."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.responses import Response

from ..bank import service as bs
from ..bank.models import CSV_COLUMNS, Question
from ..db import audit, get_db
from ..tasks import enqueue
from ..web import ApiError, Ctx, Router, not_found

router = Router("/api/banks")


def _bank(ctx: Ctx) -> dict:
    b = bs.get_bank(int(ctx.path["bank_id"]))
    if not b or (b["owner_id"] != ctx.uid and not ctx.is_admin):
        raise not_found("Question bank")
    return b


def _summary(b: dict) -> dict:
    v = b.get("validation") or {}
    counts = get_db().fetchone("SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved, "
                               "SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) AS drafts FROM questions WHERE bank_id = ?", (b["id"],))
    return {"id": b["id"], "name": b["name"], "subject": b["subject"], "description": b.get("description", ""), "version": b["version"],
            "aliases": b.get("aliases") or {}, "ready": v.get("ready", False), "errors": v.get("errors", []),
            "warnings": v.get("warnings", []), "by_phase": v.get("by_phase", {}), "topics": v.get("topics", {}),
            "total": counts["total"] or 0, "approved": counts["approved"] or 0, "drafts": counts["drafts"] or 0,
            "updated_at": b["updated_at"]}


@router.get("", auth="staff")
def list_banks(ctx: Ctx):
    rows = get_db().fetchall("SELECT * FROM question_banks WHERE owner_id = ? OR ? ORDER BY updated_at DESC", (ctx.uid, int(ctx.is_admin)))
    return {"banks": [_summary(b) for b in rows], "csv_columns": CSV_COLUMNS}


class BankIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    subject: str = Field(min_length=2, max_length=120)
    description: str = ""
    aliases: dict[str, str] = {}


@router.post("", auth="staff")
def create(ctx: Ctx):
    body = ctx.parse(BankIn)
    bid = bs.create_bank(ctx.user, body.name, body.subject, body.description, body.aliases)
    bs.refresh_validation(bid)
    audit(ctx.user, "bank_create", "question_bank", bid)
    return {"bank": _summary(bs.get_bank(bid))}


@router.get("/{bank_id:int}", auth="staff")
def get(ctx: Ctx):
    b = _bank(ctx)
    return {"bank": _summary(b), "questions": bs.questions_of(b["id"])}


@router.patch("/{bank_id:int}", auth="staff")
def update(ctx: Ctx):
    b = _bank(ctx)
    data = ctx.body or {}
    upd = {k: data[k] for k in ("name", "subject", "description", "aliases") if k in data}
    if upd:
        get_db().update("question_banks", b["id"], upd)
        bs.refresh_validation(b["id"])
    return {"bank": _summary(bs.get_bank(b["id"]))}


@router.post("/{bank_id:int}/import", auth="staff", rate=(30, 3600))
def import_file(ctx: Ctx):
    b = _bank(ctx)
    f = ctx.files.get("file")
    if not f:
        raise ApiError(422, "Attach a .csv or .json file", "no_file")
    if len(f["data"]) > 5 * 1024 * 1024:
        raise ApiError(413, "File is larger than 5 MB", "too_large")
    approve = str(ctx.form.get("approve", "true")).lower() in ("1", "true", "yes", "on")
    res = bs.import_upload(b["id"], f["filename"], f["data"], ctx.user, approve=approve)
    return {**res, "bank": _summary(bs.get_bank(b["id"]))}


@router.get("/{bank_id:int}/export", auth="staff")
def export(ctx: Ctx):
    b = _bank(ctx)
    fmt = ctx.query.get("format", "json")
    body, mime = bs.export_bank(b["id"], "csv" if fmt == "csv" else "json")
    name = f"{b['subject'].replace(' ', '_')}_bank_v{b['version']}.{'csv' if fmt == 'csv' else 'json'}"
    return Response(body, media_type=mime, headers={"content-disposition": f'attachment; filename="{name}"'})


class GenerateIn(BaseModel):
    syllabus: str = Field(min_length=20, max_length=12000)
    fundamentals: int = Field(default=6, ge=0, le=20)
    depth: int = Field(default=3, ge=0, le=10)
    applied: int = Field(default=2, ge=0, le=8)


@router.post("/{bank_id:int}/generate", auth="staff", rate=(10, 3600))
def generate(ctx: Ctx):
    b = _bank(ctx)
    body = ctx.parse(GenerateIn)
    tid = enqueue("generate_bank", {"bank_id": b["id"], "syllabus": body.syllabus,
                                    "counts": {"fundamentals": body.fundamentals, "depth": body.depth, "applied": body.applied}},
                  user_id=ctx.uid)
    audit(ctx.user, "bank_generate", "question_bank", b["id"], data={"task": tid})
    return {"task_id": tid}


@router.post("/{bank_id:int}/questions", auth="staff")
def add_question(ctx: Ctx):
    b = _bank(ctx)
    q = Question.model_validate(ctx.body or {}).model_dump() if isinstance(ctx.body, dict) else None
    if not q:
        raise ApiError(422, "Invalid question", "invalid")
    bs.add_questions(b["id"], [q], source="manual", status="approved", actor=ctx.user)
    return {"bank": _summary(bs.get_bank(b["id"])), "questions": bs.questions_of(b["id"])}


def _question(ctx: Ctx) -> dict:
    row = get_db().fetchone("SELECT q.*, b.owner_id FROM questions q JOIN question_banks b ON b.id = q.bank_id WHERE q.id = ?",
                            (int(ctx.path["qid"]),))
    if not row or (row["owner_id"] != ctx.uid and not ctx.is_admin):
        raise not_found("Question")
    return row


@router.patch("/questions/{qid:int}", auth="staff")
def edit_question(ctx: Ctx):
    _question(ctx)
    try:
        q = bs.update_question(int(ctx.path["qid"]), ctx.body or {}, ctx.user)
    except Exception as exc:  # pydantic validation
        raise ApiError(422, str(exc).split("\n")[0][:200], "invalid")
    return {"question": q}


class StatusIn(BaseModel):
    ids: list[int]
    status: str


@router.post("/{bank_id:int}/status", auth="staff")
def set_status(ctx: Ctx):
    b = _bank(ctx)
    body = ctx.parse(StatusIn)
    ids = [r["id"] for r in get_db().fetchall("SELECT id FROM questions WHERE bank_id = ?", (b["id"],)) if r["id"] in set(body.ids)]
    try:
        n = bs.set_status(ids, body.status, ctx.user)
    except ValueError:
        raise ApiError(422, "status must be approved, rejected or draft", "invalid")
    return {"updated": n, "bank": _summary(bs.get_bank(b["id"]))}


@router.delete("/questions/{qid:int}", auth="staff")
def delete_question(ctx: Ctx):
    row = _question(ctx)
    get_db().execute("DELETE FROM questions WHERE id = ?", (row["id"],))
    bs.bump_version(row["bank_id"])
    bs.refresh_validation(row["bank_id"])
    audit(ctx.user, "question_delete", "question", row["id"], before={"code": row["code"], "text": row["text"]})
    return {"ok": True}


@router.get("/tasks/{task_id:int}", auth="staff")
def task_status(ctx: Ctx):
    t = get_db().fetchone("SELECT id, kind, status, progress, message, result, error FROM tasks WHERE id = ?", (int(ctx.path["task_id"]),))
    if not t:
        raise not_found("Task")
    return {"task": t}
