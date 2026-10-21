"""Sign-in: faculty with email + password; students with the viva code + roll number (no account to create)."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from ..config import get_settings
from ..db import audit, get_db, now_iso
from ..security import SESSION_COOKIE, create_session_token, failed_logins, hash_password, verify_password
from ..vivas import CONSENT_VERSION, consent_text, join, start_or_resume
from ..web import ApiError, Ctx, Router, client_ip, public_user

router = Router("/api/auth")


def _cookie_response(body: dict, user: dict, extra: dict | None = None) -> JSONResponse:
    token = create_session_token(user["id"], user["role"], extra)
    resp = JSONResponse({**body, "token": token})
    s = get_settings()
    resp.set_cookie(SESSION_COOKIE, token, max_age=s.session_hours * 3600, httponly=True, samesite="lax",
                    secure=s.base_url.startswith("https://"))
    return resp


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login", auth="public", rate=(30, 300))
def login(ctx: Ctx):
    body = ctx.parse(LoginIn)
    email = body.email.strip().lower()
    key = f"login:{email}"
    if failed_logins.count(key, 900) >= 10:
        raise ApiError(429, "Too many failed attempts. Try again in 15 minutes.", "locked")
    user = get_db().fetchone("SELECT * FROM users WHERE lower(email) = ? AND role IN ('faculty', 'admin')", (email,))
    if not user or not user.get("password_hash") or not verify_password(body.password, user["password_hash"]):
        failed_logins.hit(key)
        raise ApiError(401, "Wrong email or password", "bad_credentials")
    get_db().update("users", user["id"], {"last_seen_at": now_iso()})
    return _cookie_response({"user": public_user(user)}, user)


class JoinIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    roll_no: str = Field(min_length=3, max_length=32)
    name: str = Field(default="", max_length=80)


@router.post("/join", auth="public", rate=(60, 300))
def student_join(ctx: Ctx):
    body = ctx.parse(JoinIn)
    try:
        student, viva = join(body.code, body.roll_no, body.name)
        sess = start_or_resume(viva, student)
    except PermissionError as exc:
        raise ApiError(403, str(exc), "not_allowed")
    except ValueError as exc:
        raise ApiError(422, str(exc), "invalid")
    audit(student, "student_join", "session", sess["id"], data={"ip": client_ip(ctx.request)})
    return _cookie_response({"user": public_user(student), "session_id": sess["id"], "status": sess["status"],
                             "viva": {"title": viva["title"], "subject": viva["subject"], "duration_s": viva["duration_s"]},
                             "consent_text": consent_text(), "consent_version": CONSENT_VERSION}, student, {"sid": sess["id"]})


@router.post("/logout", auth="public")
def logout(ctx: Ctx):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/me", auth="user")
def me(ctx: Ctx):
    return {"user": public_user(ctx.user)}


class ChangePassword(BaseModel):
    current: str
    new: str = Field(min_length=8, max_length=200)


@router.post("/password", auth="staff")
def change_password(ctx: Ctx):
    body = ctx.parse(ChangePassword)
    if not verify_password(body.current, ctx.user.get("password_hash") or ""):
        raise ApiError(403, "Current password is wrong", "bad_credentials")
    get_db().update("users", ctx.uid, {"password_hash": hash_password(body.new)})
    audit(ctx.user, "password_change", "user", ctx.uid)
    return {"ok": True}
