"""Thin routing layer on top of Starlette.

Handlers receive a `Ctx` with the authenticated user, parsed body/query and helpers.
Synchronous handlers run in Starlette's threadpool (so SQLite calls never block the event
loop); async handlers are awaited directly (used for outbound HTTP / LLM streaming).
"""
from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .db import get_db, now_iso
from .config import get_settings
from .security import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE, decode_session_token, rate_limiter

log = logging.getLogger("vaani.web")
M = TypeVar("M", bound=BaseModel)


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "error", details: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.details = details


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, f"{what} not found", "not_found")


@dataclass
class Ctx:
    request: Request
    user: dict | None
    body: Any = None
    form: dict[str, Any] = field(default_factory=dict)
    files: dict[str, dict] = field(default_factory=dict)

    @property
    def db(self):
        return get_db()

    @property
    def path(self) -> dict[str, Any]:
        return self.request.path_params

    @property
    def query(self):
        return self.request.query_params

    @property
    def uid(self) -> int:
        assert self.user is not None
        return int(self.user["id"])

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user["role"] == "admin")

    @property
    def is_staff(self) -> bool:
        return bool(self.user and self.user["role"] in ("faculty", "admin"))

    def parse(self, model: type[M]) -> M:
        data = self.body if isinstance(self.body, dict) else {}
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            errors = [
                {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()
            ]
            first = errors[0] if errors else {"field": "", "message": "Invalid input"}
            raise ApiError(422, f"{first['field']}: {first['message']}".strip(": "), "validation_error", errors)

    def qint(self, name: str, default: int, lo: int = 0, hi: int = 10_000) -> int:
        try:
            value = int(self.query.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))


def client_ip(request: Request) -> str:
    """The caller's IP. Behind a reverse proxy (TRUST_PROXY=1) the first X-Forwarded-For address is used."""
    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "anon"


def _load_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        return None
    payload = decode_session_token(token)
    if not payload:
        return None
    user = get_db().fetchone("SELECT * FROM users WHERE id = ?", (int(payload["sub"]),))
    return user


def public_user(user: dict) -> dict:
    keys = ("id", "email", "name", "role", "roll_no", "department", "created_at")
    return {k: user.get(k) for k in keys}


def to_response(result: Any) -> Response:
    if isinstance(result, Response):
        return result
    if result is None:
        return JSONResponse({"ok": True})
    return JSONResponse(result)


class Router:
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix
        self.routes: list[Route] = []

    def get(self, path: str, **kw):
        return self.add(path, ["GET"], **kw)

    def post(self, path: str, **kw):
        return self.add(path, ["POST"], **kw)

    def put(self, path: str, **kw):
        return self.add(path, ["PUT"], **kw)

    def patch(self, path: str, **kw):
        return self.add(path, ["PATCH"], **kw)

    def delete(self, path: str, **kw):
        return self.add(path, ["DELETE"], **kw)

    def add(self, path: str, methods: list[str], auth: str = "user", rate: tuple[int, int] | None = None):
        """auth: 'public' | 'user' | 'staff' (faculty or admin) | 'admin' | 'student'. rate: (limit, window_s) per user/IP."""

        def decorator(fn: Callable):
            is_async = inspect.iscoroutinefunction(fn)

            async def endpoint(request: Request) -> Response:
                try:
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
                            raise ApiError(403, "Missing CSRF header", "csrf")
                    user = await run_in_threadpool(_load_user, request) if auth != "public" or request.cookies else None
                    if auth != "public" and not user:
                        raise ApiError(401, "Please sign in", "unauthenticated")
                    if auth == "admin" and user and user["role"] != "admin":
                        raise ApiError(403, "Admins only", "forbidden")
                    if auth == "staff" and user and user["role"] not in ("faculty", "admin"):
                        raise ApiError(403, "Faculty only", "forbidden")
                    if auth == "student" and user and user["role"] != "student":
                        raise ApiError(403, "Students only", "forbidden")
                    if rate:
                        who = f"u{user['id']}" if user else client_ip(request)
                        limit = max(1, int(rate[0] * get_settings().rate_limit_scale))
                        if not rate_limiter.allow(f"{fn.__module__}.{fn.__name__}:{who}", limit, rate[1]):
                            raise ApiError(429, "Too many requests, please slow down", "rate_limited")
                    ctx = Ctx(request=request, user=user)
                    ctype = request.headers.get("content-type", "")
                    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                        if ctype.startswith("application/json"):
                            raw = await request.body()
                            if raw:
                                try:
                                    ctx.body = json.loads(raw)
                                except ValueError:
                                    raise ApiError(400, "Body is not valid JSON", "bad_json")
                        elif ctype.startswith("multipart/form-data") or ctype.startswith("application/x-www-form-urlencoded"):
                            form = await request.form(max_files=5, max_fields=50)
                            for key, value in form.multi_items():
                                if isinstance(value, UploadFile):
                                    ctx.files[key] = {
                                        "filename": value.filename or "upload",
                                        "content_type": value.content_type or "application/octet-stream",
                                        "data": await value.read(),
                                    }
                                else:
                                    ctx.form[key] = value
                            ctx.body = dict(ctx.form)
                    if is_async:
                        result = await fn(ctx)
                    else:
                        result = await run_in_threadpool(fn, ctx)
                    return to_response(result)
                except ApiError as exc:
                    body = {"error": exc.message, "code": exc.code}
                    if exc.details is not None:
                        body["details"] = exc.details
                    return JSONResponse(body, status_code=exc.status)
                except Exception:  # pragma: no cover - logged, generic message to client
                    log.exception("Unhandled error in %s %s", request.method, request.url.path)
                    return JSONResponse({"error": "Something went wrong on our side", "code": "server_error"},
                                        status_code=500)

            endpoint.__name__ = fn.__name__
            self.routes.append(Route(self.prefix + path, endpoint, methods=methods))
            return fn

        return decorator


def touch_user(user_id: int) -> None:
    get_db().execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now_iso(), user_id))
