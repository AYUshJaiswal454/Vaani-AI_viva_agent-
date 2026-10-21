"""HTTP API: sign-in, joining rules, viva and bank management, exports, access control and privacy."""
from __future__ import annotations

import csv
import io
import re
import subprocess
import unittest
from pathlib import Path

from tests.support import AppTestCase, BANKS, H, drain

ROOT = Path(__file__).resolve().parent.parent


class Auth(AppTestCase):
    def test_faculty_login_and_lockout(self):
        c = self.client()
        r = c.post("/api/auth/login", json={"email": "faculty@test.local", "password": "wrong"}, headers=H)
        self.assertEqual(r.status_code, 401)
        r = c.post("/api/auth/login", json={"email": "faculty@test.local", "password": "password123"}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get("/api/auth/me").json()["user"]["role"], "faculty")

    def test_writes_need_csrf_header(self):
        c = self.client()
        r = c.post("/api/auth/login", json={"email": "faculty@test.local", "password": "password123"})
        self.assertEqual(r.status_code, 403)

    def test_students_cannot_use_faculty_endpoints(self):
        fac = self.faculty()
        v = self.make_viva(fac, self.make_bank(fac))
        st = self.student(v["access_code"], roll="22CS5001")
        self.assertEqual(st.get("/api/vivas", headers=H).status_code, 403)
        self.assertEqual(st.get("/api/banks", headers=H).status_code, 403)
        self.assertEqual(st.get("/api/system/status", headers=H).status_code, 403)


class Joining(AppTestCase):
    def setUp(self):
        self.fac = self.faculty()
        if not getattr(self.__class__, "bank", None):
            self.__class__.bank = self.make_bank(self.fac)

    def test_wrong_code_closed_viva_and_roster(self):
        c = self.client()
        r = c.post("/api/auth/join", json={"code": "ZZZZZZ", "roll_no": "22CS1"}, headers=H)
        self.assertEqual(r.status_code, 403)
        v = self.make_viva(self.fac, self.bank, open_=False)
        r = c.post("/api/auth/join", json={"code": v["access_code"], "roll_no": "22CS1001"}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("not open", r.json()["error"])
        v2 = self.make_viva(self.fac, self.bank, roster=["22CS1001, Asha Verma", "22CS1002, Rahul Nair"])
        r = c.post("/api/auth/join", json={"code": v2["access_code"], "roll_no": "22CS9999"}, headers=H)
        self.assertEqual(r.status_code, 403)
        r = c.post("/api/auth/join", json={"code": v2["access_code"].lower(), "roll_no": "22cs1002"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["user"]["name"], "Rahul Nair")

    def test_one_attempt_and_resume(self):
        v = self.make_viva(self.fac, self.bank)
        a = self.student(v["access_code"], roll="22CS6001")
        b = self.student(v["access_code"], roll="22CS6001")
        self.assertEqual(a.session_id, b.session_id, "an unfinished attempt is resumed, not duplicated")
        from app.db import get_db

        get_db().update("sessions", a.session_id, {"status": "scored"})
        c = self.client()
        r = c.post("/api/auth/join", json={"code": v["access_code"], "roll_no": "22CS6001"}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("already completed", r.json()["error"])

    def test_session_plan_snapshots_the_questions(self):
        v = self.make_viva(self.fac, self.bank)
        st = self.student(v["access_code"], roll="22CS6002")
        from app.vivas import get_session

        sess = get_session(st.session_id)
        snap = sess["plan"]["snapshot"]
        self.assertTrue(all(c in snap for c in sess["plan"]["fundamentals"]))
        self.assertEqual(sess["bank_version"], self.fac.get(f"/api/banks/{self.bank}", headers=H).json()["bank"]["version"])

    def test_consent_needed(self):
        v = self.make_viva(self.fac, self.bank)
        st = self.student(v["access_code"], roll="22CS6003", consent=False)
        info = st.get("/api/student/session", headers=H).json()
        self.assertFalse(info["session"]["consented"])
        self.assertIn("recorded", info["consent_text"])
        r = st.post("/api/student/consent", json={"accept": False}, headers=H)
        self.assertEqual(r.status_code, 422)


class VivaAndBank(AppTestCase):
    def test_bank_upload_validation_and_approval(self):
        fac = self.faculty()
        r = fac.post("/api/banks", json={"name": "Mini", "subject": "Computer Networks"}, headers=H)
        bid = r.json()["bank"]["id"]
        csv_text = ("topic,phase,difficulty,question,key_points,hints\n"
                    "TCP,fundamentals,1,What does TCP guarantee that UDP does not?,Reliable delivery: reliable / retransmit; Ordering: in order / sequence numbers,Think of a phone call versus a postcard.\n"
                    "TCP,fundamentals,2,Why does TCP use a three way handshake?,Synchronise sequence numbers: sequence numbers / syn,Think about both sides agreeing before talking.\n"
                    "IP,fundamentals,1,What is the purpose of a subnet mask?,Splits network and host: network part / host part,Think of a postal code.\n"
                    "TCP,depth,3,How does TCP congestion control react to packet loss?,Reduce window: congestion window / halve,Think of traffic on a highway.\n"
                    "Apps,applied,2,A website loads slowly only for users far away. How would you investigate?,Latency: latency / round trip / cdn,Think about distance.\n")
        r = fac.post(f"/api/banks/{bid}/import", files={"file": ("cn.csv", csv_text.encode(), "text/csv")}, data={"approve": "false"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["added"], 5)
        bank = r.json()["bank"]
        self.assertFalse(bank["ready"], "drafts don't count until approved")
        ids = [q["id"] for q in fac.get(f"/api/banks/{bid}", headers=H).json()["questions"]]
        r = fac.post(f"/api/banks/{bid}/status", json={"ids": ids, "status": "approved"}, headers=H)
        self.assertTrue(r.json()["bank"]["ready"])
        v1 = r.json()["bank"]["version"]
        q = fac.get(f"/api/banks/{bid}", headers=H).json()["questions"][0]
        r = fac.patch(f"/api/banks/questions/{q['id']}", json={"question": q["question"] + " Explain briefly."}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertGreater(fac.get(f"/api/banks/{bid}", headers=H).json()["bank"]["version"], v1)
        exp = fac.get(f"/api/banks/{bid}/export?format=csv", headers=H)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(exp.text)))), 5)

    def test_generate_drafts_offline(self):
        fac = self.faculty()
        bid = fac.post("/api/banks", json={"name": "Gen", "subject": "Operating Systems"}, headers=H).json()["bank"]["id"]
        r = fac.post(f"/api/banks/{bid}/generate", json={"syllabus": "Unit 1: Processes: creation, states, context switch\n"
                                                                     "Unit 2: Scheduling: FCFS, SJF, round robin"}, headers=H)
        self.assertEqual(r.status_code, 200)
        drain()
        t = fac.get(f"/api/banks/tasks/{r.json()['task_id']}", headers=H).json()["task"]
        self.assertEqual(t["status"], "done", t)
        qs = fac.get(f"/api/banks/{bid}", headers=H).json()["questions"]
        self.assertTrue(qs)
        self.assertTrue(all(q["status"] == "draft" and q["source"] == "ai" for q in qs))

    def test_viva_needs_ready_bank_and_justified_custom_plan(self):
        fac = self.faculty()
        empty = fac.post("/api/banks", json={"name": "Empty", "subject": "Empty subject"}, headers=H).json()["bank"]["id"]
        v = fac.post("/api/vivas", json={"bank_id": empty, "title": "x"}, headers=H).json()["viva"]
        r = fac.post(f"/api/vivas/{v['id']}/status", json={"status": "open"}, headers=H)
        self.assertEqual(r.status_code, 422)
        bank = self.make_bank(fac)
        phases = [{"id": "warmup", "start": 0, "end": 90}, {"id": "fundamentals", "start": 90, "end": 300},
                  {"id": "depth", "start": 300, "end": 600}, {"id": "applied", "start": 600, "end": 780},
                  {"id": "wrapup", "start": 780, "end": 870}]
        r = fac.post("/api/vivas", json={"bank_id": bank, "phase_plan": {"phases": phases}}, headers=H)
        self.assertEqual(r.status_code, 422)
        r = fac.post("/api/vivas", json={"bank_id": bank, "phase_plan": {"phases": phases, "justification": "Longer project discussion for the final-year batch."}}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["viva"]["phase_plan"]["phases"][0]["end"], 90)

    def test_export_csv_and_sheets_link(self):
        fac = self.faculty()
        v = self.make_viva(fac, self.make_bank(fac))
        self.student(v["access_code"], roll="22CS7001")
        r = fac.get(f"/api/vivas/{v['id']}/export.csv", headers=H)
        rows = list(csv.reader(io.StringIO(r.text)))
        self.assertIn("final_total", rows[0])
        self.assertEqual(rows[1][0], "22CS7001")
        link = fac.get(f"/api/vivas/{v['id']}/sheets-link", headers=H).json()
        token_path = link["url"].split("/api/", 1)[1]
        pub = self.client().get("/api/" + token_path)
        self.assertEqual(pub.status_code, 200)
        self.assertIn("22CS7001", pub.text)
        self.assertEqual(self.client().get("/api/public/sheet/forged.csv").status_code, 403)

    def test_other_faculty_cannot_open_vivas(self):
        fac = self.faculty()
        v = self.make_viva(fac, self.make_bank(fac))
        other = self.faculty("other@test.local")
        self.assertEqual(other.get(f"/api/vivas/{v['id']}", headers=H).status_code, 404)
        self.assertEqual(other.get(f"/api/vivas/{v['id']}/export.csv", headers=H).status_code, 404)


class Privacy(AppTestCase):
    def test_recordings_need_permission(self):
        fac = self.faculty()
        v = self.make_viva(fac, self.make_bank(fac))
        st = self.student(v["access_code"], roll="22CS8001")
        anon = self.client()
        self.assertEqual(anon.get(f"/api/sessions/{st.session_id}/audio").status_code, 403)
        self.assertEqual(anon.get(f"/api/sessions/{st.session_id}/audio?t=forged").status_code, 403)
        other = self.faculty("other@test.local")
        self.assertEqual(other.get(f"/api/sessions/{st.session_id}/audio").status_code, 403)

    def test_tts_clips_need_sign_in(self):
        self.assertEqual(self.client().get("/api/tts/" + "a" * 32).status_code, 401)

    def test_security_headers(self):
        r = self.client().get("/api/health")
        self.assertIn("microphone=(self)", r.headers["permissions-policy"])
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])

    def test_retention_cleanup(self):
        from app.db import get_db, iso_in
        from app.tasks import enqueue

        fac = self.faculty()
        v = self.make_viva(fac, self.make_bank(fac))
        st = self.student(v["access_code"], roll="22CS8002")
        d = self.data_dir / "sessions" / str(st.session_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "conversation.ogg").write_bytes(b"x")
        get_db().update("sessions", st.session_id, {"ended_at": iso_in(days=-400), "status": "scored"})
        enqueue("cleanup", {})
        drain()
        self.assertFalse(d.exists())


    def test_admin_can_erase_a_recording_or_the_attempt(self):
        from app.config import get_settings
        from app.db import get_db

        fac = self.faculty()
        v = self.make_viva(fac, self.make_bank(fac))
        st = self.student(v["access_code"], roll="22CS8003")
        d = self.data_dir / "sessions" / str(st.session_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "conversation.ogg").write_bytes(b"x")
        db = get_db()
        db.update("sessions", st.session_id, {"status": "scored"})
        body = {"reason": "student asked for erasure", "scope": "recording"}
        self.assertEqual(fac.post(f"/api/sessions/{st.session_id}/erase", json=body, headers=H).status_code, 403)
        adm = self.client()
        s = get_settings()
        r = adm.post("/api/auth/login", json={"email": s.admin_email, "password": s.admin_password}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        r = adm.post(f"/api/sessions/{st.session_id}/erase", json=body, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(d.exists())
        self.assertIsNotNone(db.fetchone("SELECT id FROM sessions WHERE id = ?", (st.session_id,)))
        r = adm.post(f"/api/sessions/{st.session_id}/erase", json={**body, "scope": "everything"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(db.fetchone("SELECT id FROM sessions WHERE id = ?", (st.session_id,)))
        actions = {a["action"] for a in db.fetchall("SELECT action FROM audit_log WHERE entity = 'session' AND entity_id = ?",
                                                     (str(st.session_id),))}
        self.assertTrue({"erase_recording", "erase_everything"} <= actions)


class SampleVivas(unittest.TestCase):
    def test_sample_vivas_load_into_a_fresh_server(self):
        """The five recorded samples in samples/vivas/ appear in the demo dashboard on first start, re-encrypted."""
        import json

        from app.config import get_settings
        from app.db import get_db
        from app.samples import SAMPLES_DIR, import_samples
        from app.voice.audio import read_secure
        from tests.support import fresh_environment

        if not (SAMPLES_DIR / "index.json").exists():
            self.skipTest("no exported samples")
        fresh_environment(seed_demo=True, seed_samples=True)
        from app.main import seed

        seed()
        db = get_db()
        index = json.loads((SAMPLES_DIR / "index.json").read_text())
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sessions"), len(index))
        s = db.fetchone("SELECT * FROM sessions ORDER BY id LIMIT 1")
        self.assertIn(s["status"], ("scored", "reviewed"))
        self.assertGreater(db.scalar("SELECT COUNT(*) FROM turns WHERE session_id = ?", (s["id"],)), 10)
        path = get_settings().sessions_dir / str(s["id"]) / "conversation.ogg"
        self.assertFalse(path.read_bytes().startswith(b"OggS"), "stored encrypted")
        self.assertTrue(read_secure(path).startswith(b"OggS"))
        self.assertEqual(import_samples(), 0, "importing twice adds nothing")


class NoSecretsInRepo(unittest.TestCase):
    """The brief: API keys must never be committed. Scan every file that would be committed."""

    PATTERNS = [r"sk-[A-Za-z0-9_-]{20,}", r"gsk_[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
                r"(?i)(api[_-]?key|secret)\s*=\s*['\"][A-Za-z0-9_\-]{24,}['\"]"]

    def test_no_keys(self):
        try:
            files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True,
                                   text=True, timeout=30).stdout.split()
        except Exception:
            files = []
        if not files:
            files = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "data" not in p.parts]
        hits = []
        for f in files:
            p = ROOT / f
            if not p.is_file() or p.suffix in (".png", ".jpg", ".ogg", ".mp3", ".wav", ".ttf", ".onnx", ".pdf", ".mp4", ".bz2"):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for pat in self.PATTERNS:
                if re.search(pat, text):
                    hits.append((f, pat))
        self.assertEqual(hits, [])

    def test_env_files_are_ignored(self):
        gi = (ROOT / ".gitignore").read_text()
        self.assertIn(".env", gi)
        self.assertIn("/data/", gi)
        self.assertIn("/models/", gi)


if __name__ == "__main__":
    unittest.main()
