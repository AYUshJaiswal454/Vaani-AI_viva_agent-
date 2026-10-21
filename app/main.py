"""Vaani ASGI application: REST API, the live viva WebSocket, and the built web app."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from .config import DATA_FILES, get_settings
from .db import get_db, now_iso
from .metrics import metrics
from .security import hash_password

log = logging.getLogger("vaani")
STATIC_DIR = Path(__file__).parent / "static"

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), geolocation=(), microphone=(self)",
    "content-security-policy": (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; script-src 'self'; connect-src 'self' ws: wss:; worker-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class HeadersAndMetricsMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                existing = {k.decode().lower() for k, _ in headers}
                for key, value in SECURITY_HEADERS.items():
                    if key not in existing:
                        headers.append((key.encode(), value.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope.get("path", "").startswith("/api/"):
                endpoint = scope.get("endpoint")
                metrics.observe(scope.get("method", "GET"), getattr(endpoint, "__name__", None) or "unmatched",
                                status_holder["status"], time.perf_counter() - start)


DEMO_FACULTY = {"email": "faculty@vaani.local", "password": "faculty123", "name": "Dr. Meera Iyer", "department": "Computer Science"}
DEMO_VIVAS = [("dbms.json", "DBMS26", "DBMS mid-semester viva"), ("os.json", "OSYS26", "Operating Systems viva")]


def seed() -> None:
    try:
        _seed()
    except Exception as exc:  # another process seeded at the same moment
        if "UNIQUE" not in str(exc).upper() and "duplicate" not in str(exc).lower():
            raise


def _seed() -> None:
    s = get_settings()
    db = get_db()
    if not db.fetchone("SELECT id FROM users WHERE email = ?", (s.admin_email,)):
        db.insert("users", {"role": "admin", "name": "Vaani Admin", "email": s.admin_email,
                            "password_hash": hash_password(s.admin_password), "settings": {}, "created_at": now_iso()})
    if not s.seed_demo:
        return
    fac = db.fetchone("SELECT * FROM users WHERE email = ?", (DEMO_FACULTY["email"],))
    if not fac:
        db.insert("users", {"role": "faculty", "name": DEMO_FACULTY["name"], "email": DEMO_FACULTY["email"],
                            "password_hash": hash_password(DEMO_FACULTY["password"]), "department": DEMO_FACULTY["department"],
                            "settings": {}, "created_at": now_iso()})
        fac = db.fetchone("SELECT * FROM users WHERE email = ?", (DEMO_FACULTY["email"],))
    from .bank.service import load_bank_file
    from .vivas import create_viva, set_status

    for fname, code, title in DEMO_VIVAS:
        if db.fetchone("SELECT id FROM vivas WHERE access_code = ?", (code,)):
            continue
        bank_id = load_bank_file(fac, DATA_FILES / "banks" / fname)
        viva = create_viva(fac, {"bank_id": bank_id, "title": title, "difficulty": "mixed"})
        db.update("vivas", viva["id"], {"access_code": code})
        set_status(db.fetchone("SELECT * FROM vivas WHERE id = ?", (viva["id"],)), "open", fac)
        log.info("Seeded demo viva %s (code %s)", title, code)
    if s.seed_samples and not db.scalar("SELECT COUNT(*) FROM sessions"):
        from .samples import import_samples

        import_samples()


def warm_models() -> None:
    """Load the speech models before the first student speaks (the first Whisper call otherwise pays ~3 s to load)."""
    s = get_settings()
    try:
        from .voice.vad import model_available

        model_available(s.models_dir)
        if s.stt_engine == "whisper-onnx":
            from .voice.stt import local_pool, workers

            local_pool().warm()
            log.info("Speech recogniser ready: whisper-%s x %d workers", s.whisper_model, workers())
    except Exception as exc:  # noqa: BLE001 - a missing model must not stop the server
        log.warning("Speech model warm-up skipped: %s", exc)


def _spa_index(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found", "code": "not_found"}, status_code=404)
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"cache-control": "no-cache"})
    return PlainTextResponse("Frontend not built. Run `node build.mjs` in frontend/.", status_code=503)


async def health(request: Request) -> Response:
    from .live import ACTIVE
    from .llm.providers import provider_status
    from .voice import stt as stt_mod

    db_ok = True
    try:
        get_db().scalar("SELECT 1")
    except Exception:  # pragma: no cover
        db_ok = False
    s = get_settings()
    return JSONResponse({"ok": db_ok, "time": now_iso(), "live_sessions": len(ACTIVE),
                         "queue_depth": get_db().scalar("SELECT COUNT(*) FROM tasks WHERE status = 'queued'"),
                         "ai": provider_status(), "stt": s.stt_engine, "tts": s.tts_engine, "stt_queue": stt_mod.queue_depth()})


async def metrics_endpoint(request: Request) -> Response:
    from .live import ACTIVE

    queued = get_db().scalar("SELECT COUNT(*) FROM tasks WHERE status = 'queued'") or 0
    return PlainTextResponse(metrics.render(extra={"vaani_queue_depth": queued, "vaani_live_sessions": len(ACTIVE)}))


def create_app(start_worker: bool | None = None) -> Starlette:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from . import pipelines  # noqa: F401  (registers task handlers)
    from .api import routes as api_routes
    from .live import ws_endpoint

    @contextlib.asynccontextmanager
    async def lifespan(app):
        get_db()
        seed()
        asyncio.get_running_loop().run_in_executor(None, warm_models)
        stop = asyncio.Event()
        background: list[asyncio.Task] = []
        worker = None
        run_worker = settings.embedded_worker if start_worker is None else start_worker
        if run_worker:
            from .tasks import Worker, scheduler_loop

            worker = Worker(concurrency=settings.worker_concurrency)
            background.append(asyncio.create_task(worker.run()))
            background.append(asyncio.create_task(scheduler_loop(stop, worker.id)))
        try:
            yield
        finally:
            stop.set()
            if worker:
                await worker.stop()
            for task in background:
                task.cancel()

    routes = [
        Route("/api/health", health),
        Route("/metrics", metrics_endpoint),
        WebSocketRoute("/ws/session/{session_id:int}", ws_endpoint),
        *api_routes(),
    ]
    if (STATIC_DIR / "assets").exists():
        routes.append(Mount("/assets", app=StaticFiles(directory=STATIC_DIR / "assets"), name="assets"))
    routes.append(Route("/favicon.svg", lambda r: FileResponse(STATIC_DIR / "favicon.svg") if (STATIC_DIR / "favicon.svg").exists()
                        else PlainTextResponse("", 404)))
    routes.append(Route("/{path:path}", _spa_index))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(HeadersAndMetricsMiddleware)
    return app


app = create_app()
