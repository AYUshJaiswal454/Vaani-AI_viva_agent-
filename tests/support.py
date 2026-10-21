"""Shared helpers for the test-suite (stdlib unittest; also runs under pytest).

Every test class gets its own temporary data directory and SQLite database, an app instance without the background
worker (tasks are drained inline), the deterministic offline AI mode, the stub speech recogniser and the stub voice,
unless a test opts in to something else.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

warnings.simplefilter("ignore", ResourceWarning)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")

try:  # Starlette's TestClient needs httpx; some sandboxes ship a compatible fork under another name.
    import httpx  # noqa: F401
except ImportError:  # pragma: no cover
    import httpx2  # type: ignore

    sys.modules["httpx"] = httpx2

from starlette.testclient import TestClient  # noqa: E402

H = {"X-Requested-With": "vaani"}
BANKS = ROOT / "app" / "data" / "banks"
FAST_PHASES = [{"id": "warmup", "start": 0, "end": 60}, {"id": "fundamentals", "start": 60, "end": 300},
               {"id": "depth", "start": 300, "end": 600}, {"id": "applied", "start": 600, "end": 780},
               {"id": "wrapup", "start": 780, "end": 870}]


def fresh_environment(**overrides) -> Path:
    from app.config import reset_settings
    from app.db import reset_db

    data_dir = Path(tempfile.mkdtemp(prefix="vaani-test-"))
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ.pop("DATABASE_URL", None)
    defaults = {"data_dir": data_dir, "llm_provider": "mock", "stt_engine": "stub", "tts_engine": "stub",
                "seed_demo": False, "embedded_worker": False, "allow_test_hooks": True, "stub_stt_latency_ms": 5, "rate_limit_scale": 50.0,
                "models_dir": ROOT / "models"}
    defaults.update(overrides)
    reset_settings(**defaults)
    reset_db()
    return data_dir


def drain() -> int:
    import app.pipelines  # noqa: F401
    from app.tasks import drain as _drain

    total = 0
    while True:
        n = asyncio.run(_drain(500))
        total += n
        if not n:
            return total


def load_bank(name: str = "dbms") -> dict:
    return json.loads((BANKS / f"{name}.json").read_text(encoding="utf-8"))


def approved_questions(name: str = "dbms") -> tuple[list[dict], dict]:
    from app.bank.models import parse_upload

    raw = (BANKS / f"{name}.json").read_bytes()
    bank, errors = parse_upload(f"{name}.json", raw)
    assert not errors, errors
    return [dict(q, status="approved") for q in bank["questions"]], bank.get("aliases") or {}


class AppTestCase(unittest.TestCase):
    """A running app (no worker thread) with helpers for faculty and student clients."""

    settings_overrides: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.data_dir = fresh_environment(**dict(cls.settings_overrides))
        from app.main import create_app, seed

        cls.app = create_app(start_worker=False)
        seed()
        cls._clients: list[TestClient] = []
        from app.db import get_db
        from app.security import hash_password
        from app.db import now_iso

        db = get_db()
        for email, name in (("faculty@test.local", "Dr. Test Faculty"), ("other@test.local", "Dr. Other")):
            if not db.fetchone("SELECT id FROM users WHERE email = ?", (email,)):
                db.insert("users", {"role": "faculty", "name": name, "email": email, "password_hash": hash_password("password123"),
                                    "settings": {}, "created_at": now_iso()})

    @classmethod
    def tearDownClass(cls) -> None:
        for c in cls._clients:
            try:
                c.__exit__(None, None, None)
            except Exception:
                pass

    def client(self) -> TestClient:
        c = TestClient(self.app)
        c.__enter__()
        self._clients.append(c)
        return c

    def faculty(self, email: str = "faculty@test.local") -> TestClient:
        c = self.client()
        r = c.post("/api/auth/login", json={"email": email, "password": "password123"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        c.token = r.json()["token"]  # type: ignore[attr-defined]
        return c

    def make_bank(self, fac: TestClient, name: str = "dbms") -> int:
        r = fac.post("/api/banks", json={"name": f"{name} bank", "subject": load_bank(name)["subject"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        bid = r.json()["bank"]["id"]
        r = fac.post(f"/api/banks/{bid}/import", files={"file": (f"{name}.json", (BANKS / f"{name}.json").read_bytes(), "application/json")},
                     data={"approve": "true"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return bid

    def make_viva(self, fac: TestClient, bank_id: int, open_: bool = True, **extra) -> dict:
        body = {"bank_id": bank_id, "title": "Test viva", **extra}
        r = fac.post("/api/vivas", json=body, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()["viva"]
        if open_:
            r = fac.post(f"/api/vivas/{v['id']}/status", json={"status": "open"}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            drain()
        return v

    def student(self, code: str, roll: str = "22CS1047", name: str = "Asha Verma", consent: bool = True) -> TestClient:
        c = self.client()
        r = c.post("/api/auth/join", json={"code": code, "roll_no": roll, "name": name}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        c.token = r.json()["token"]  # type: ignore[attr-defined]
        c.session_id = r.json()["session_id"]  # type: ignore[attr-defined]
        if consent:
            r = c.post("/api/student/consent", json={"accept": True, "client": {"test": "1"}}, headers=H)
            self.assertEqual(r.status_code, 200, r.text)
        return c
