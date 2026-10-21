"""The live viva over the real WebSocket: audio in, examiner out, recording, rejoin after a drop, barge-in, mic checks."""
from __future__ import annotations

import json
import struct
import time
import unittest

import numpy as np

from tests.support import AppTestCase, H, drain

from loadtest.bot import synth

SR = 16000
CHUNK = 1600


class Stream:
    """A fake browser: sends 100 ms PCM frames with sequence numbers and sample offsets."""

    def __init__(self, ws):
        self.ws = ws
        self.seq = 0
        self.offset = 0
        self.inbox: list[dict] = []

    def frame(self, x: np.ndarray, started: bool = True) -> None:
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        self.seq += 1
        self.ws.send_bytes(b"\x01" + struct.pack(">II", self.seq, self.offset if started else 0) + pcm)
        if started:
            self.offset += len(x)

    def audio(self, x: np.ndarray, realtime: float = 0.0) -> None:
        for i in range(0, len(x), CHUNK):
            c = x[i:i + CHUNK]
            if len(c) < CHUNK:
                c = np.pad(c, (0, CHUNK - len(c)))
            self.frame(c)
            if realtime:
                time.sleep(0.1 / realtime)

    def silence(self, seconds: float, realtime: float = 0.0) -> None:
        self.audio(np.zeros(int(seconds * SR), np.float32), realtime)

    def recv(self, until: str, timeout: float = 20.0) -> dict:
        """Receive messages until one of type `until` arrives (others are kept in inbox)."""
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(self.ws.receive_text())
            self.inbox.append(m)
            if m["type"] == until:
                return m
        raise AssertionError(f"no {until} within {timeout}s; got {[x['type'] for x in self.inbox[-10:]]}")

    def play(self, say: dict) -> None:
        self.ws.send_text(json.dumps({"type": "playback", "event": "start", "utt": say["utt"], "t": time.time() * 1000}))
        self.ws.send_text(json.dumps({"type": "playback", "event": "end", "utt": say["utt"], "t": time.time() * 1000}))


class LiveViva(AppTestCase):
    def viva_code(self) -> str:
        if not getattr(self.__class__, "code", None):
            fac = self.faculty()
            bank = self.make_bank(fac)
            v = self.make_viva(fac, bank)
            self.__class__.code = v["access_code"]
        return self.__class__.code

    def connect(self, st):
        return st.websocket_connect(f"/ws/session/{st.session_id}?token={st.token}")

    def speak_and_wait(self, s: Stream, text: str) -> dict:
        s.ws.send_text(json.dumps({"type": "bot_hint", "text": text}))
        s.silence(0.3)
        s.audio(synth(text, "kal16"))
        s.silence(1.4)                         # end of speech: 900 ms of silence triggers the endpoint
        return s.recv("say", 15)

    def test_full_flow_drop_rejoin_and_finish(self):
        from app.db import get_db
        from app.voice.audio import ENC_MAGIC

        st = self.student(self.viva_code(), roll="22CS4001", name="Asha Verma")
        with self.connect(st) as ws:
            s = Stream(ws)
            hello = s.recv("hello")
            self.assertFalse(hello["resumed"])
            ws.send_text(json.dumps({"type": "start"}))
            greet = s.recv("say")
            self.assertIn("roll number", greet["segments"][0]["text"])
            self.assertTrue(greet["segments"][0]["audio"], "stub voice should return a clip URL")
            s.play(greet)
            say = self.speak_and_wait(s, "My name is Asha Verma and my roll number is 22CS4001.")
            self.assertEqual(say["kind"], "question")
            self.assertTrue(say["code"].startswith("DBMS-W-"))
            s.play(say)
            say2 = self.speak_and_wait(s, "I built a canteen ordering app with React, Node and PostgreSQL. I designed the tables: users, "
                                          "orders and menu items with foreign keys, and I wrote the payment part myself.")
            self.assertIn(say2["kind"], ("followup", "question", "ack"))
            while say2.get("more"):
                say2 = s.recv("say")
            s.play(say2)
            last_code = say2["code"]
        # network drop: reconnect and resume at the same question
        with self.connect(st) as ws:
            s2 = Stream(ws)
            hello = s2.recv("hello")
            self.assertTrue(hello["resumed"])
            replay = s2.recv("say")
            self.assertTrue(replay.get("resumed"))
            self.assertEqual(replay["code"], last_code)
            s2.offset = hello["rec_samples"]
            s2.seq = hello["last_seq"]
            s2.play(replay)
            ws.send_text(json.dumps({"type": "end_early"}))
            close = s2.recv("say")
            self.assertEqual(close["kind"], "close")
            s2.play(close)
            end = s2.recv("end")
            self.assertEqual(end["reason"], "ended_by_student")
        db = get_db()
        sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (st.session_id,))
        self.assertEqual(sess["status"], "ended")
        kinds = {e["kind"] for e in db.fetchall("SELECT kind FROM integrity_events WHERE session_id = ?", (st.session_id,))}
        self.assertIn("network_drop", kinds)
        self.assertIn("rejoin", kinds)
        turns = db.fetchall("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (st.session_id,))
        student = [t for t in turns if t["speaker"] == "student"]
        self.assertGreaterEqual(len(student), 2)
        self.assertIn("canteen", student[1]["text"])
        self.assertTrue(student[1]["t_end"] > student[1]["t_start"] > 0)
        raw = (self.data_dir / "sessions" / str(st.session_id) / "student.pcm").read_bytes()
        self.assertTrue(raw.startswith(ENC_MAGIC), "recording must be encrypted at rest")
        drain()                                  # finalize: mixed recording, integrity analysis, scoring
        sess = db.fetchone("SELECT * FROM sessions WHERE id = ?", (st.session_id,))
        self.assertIn(sess["status"], ("scored", "reviewed"))
        conv = self.data_dir / "sessions" / str(st.session_id) / "conversation.ogg"
        self.assertTrue(conv.exists())
        self.assertGreater(sess["client"]["recording"]["duration_s"], 3)
        fac = self.faculty()
        d = fac.get(f"/api/sessions/{st.session_id}", headers=H).json()
        r = fac.get(d["session"]["audio_url"], headers={**H, "range": "bytes=0-99"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(r.content), 100)
        self.assertTrue(r.content.startswith(b"OggS"))

    def test_barge_in_stops_the_examiner(self):
        st = self.student(self.viva_code(), roll="22CS4002", name="Rahul Nair")
        with self.connect(st) as ws:
            s = Stream(ws)
            s.recv("hello")
            ws.send_text(json.dumps({"type": "start"}))
            greet = s.recv("say")
            ws.send_text(json.dumps({"type": "playback", "event": "start", "utt": greet["utt"], "t": time.time() * 1000}))
            s.audio(synth("My name is Rahul Nair and my roll number is 22CS4002, sorry to interrupt.", "kal16"))
            m = s.recv("stop_audio", 10)
            self.assertEqual(m["reason"], "barge_in")

    def test_silent_microphone_warning(self):
        st = self.student(self.viva_code(), roll="22CS4003", name="Meena Das")
        with self.connect(st) as ws:
            s = Stream(ws)
            s.recv("hello")
            ws.send_text(json.dumps({"type": "start"}))
            greet = s.recv("say")
            s.play(greet)
            s.silence(7.0, realtime=1.0)          # digital silence while the examiner waits for an answer
            m = s.recv("warning", 5)
            self.assertEqual(m["code"], "mic_silent")

    def test_pause_keeps_speculative_text_and_tail_is_ready_at_endpoint(self):
        """Streaming recognition: a mid-answer pause (350-900 ms) starts recognising the speech so far; when the
        student carries on, that result is kept as a committed chunk (not recomputed), and at the real endpoint the
        tail has already been recognised, so nothing is transcribed twice."""
        import asyncio
        from unittest import mock

        from app import live
        from app.voice import stt as stt_mod

        calls: list[float] = []

        async def fake(pcm, **kw):
            calls.append(len(pcm) / 2 / SR)
            n = len(calls)
            for _ in range(10):                       # 50 ms of "compute", abandoning early if cancelled
                if kw.get("cancel") is not None and kw["cancel"].is_set():
                    return stt_mod.STTResult("", None, "fake", "fake", 0, 0, error="cancelled")
                await asyncio.sleep(0.005)
            return stt_mod.STTResult(f"part{n}", 0.9, "fake", "fake", 50, len(pcm) / 2 / SR)

        st = self.student(self.viva_code(), roll="22CS4006", name="Ishaan Bose")
        with mock.patch.object(live.LiveSession, "_chunkable", lambda self: True), mock.patch.object(stt_mod, "transcribe", fake):
            with self.connect(st) as ws:
                s = Stream(ws)
                s.recv("hello")
                ws.send_text(json.dumps({"type": "start"}))
                s.play(s.recv("say"))
                s.silence(0.3)
                s.audio(synth("My name is Ishaan Bose", "kal16"))
                s.silence(0.6)                            # a pause, shorter than the 900 ms endpoint
                s.audio(synth("and my roll number is 22CS4006", "kal16"))
                s.silence(1.4)
                s.recv("say", 15)
        from app.db import get_db

        turn = get_db().fetchone("SELECT * FROM turns WHERE session_id = ? AND speaker = 'student' ORDER BY seq LIMIT 1", (st.session_id,))
        self.assertEqual(turn["text"], "part1 part2")
        self.assertEqual(len(calls), 2, f"each stretch of speech is recognised exactly once, got {calls}")
        self.assertEqual(turn["meta"]["stt"]["chunks"], 2)

    def test_websocket_requires_consent_and_ownership(self):
        st = self.student(self.viva_code(), roll="22CS4004", consent=False)
        with self.connect(st) as ws:
            m = json.loads(ws.receive_text())
            self.assertEqual(m["code"], "consent_required")
        other = self.student(self.viva_code(), roll="22CS4005")
        from starlette.websockets import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect):
            with other.websocket_connect(f"/ws/session/{st.session_id}?token={other.token}") as ws:
                ws.receive_text()


class VAD(unittest.TestCase):
    def test_speech_detected_noise_ignored(self):
        from pathlib import Path

        from app.voice.vad import StreamVAD
        from loadtest.bot import noise

        models = Path(__file__).resolve().parent.parent / "models"
        rng = np.random.default_rng(0)
        v = StreamVAD(models)
        ev = v.feed((noise(SR * 4, rng) * 0.02).astype(np.float32))
        self.assertEqual([e for e in ev if e.kind == "speech_start"], [], "background noise must not start an answer")
        speech = synth("Atomicity means the whole transaction happens or nothing happens.", "kal16")
        v2 = StreamVAD(models)
        noisy = speech + noise(len(speech), rng) * 0.01
        ev = v2.feed(np.concatenate([np.zeros(SR // 2, np.float32), noisy, np.zeros(SR * 2, np.float32)]).astype(np.float32))
        kinds = [e.kind for e in ev]
        self.assertIn("speech_start", kinds)
        self.assertIn("speech_end", kinds)


if __name__ == "__main__":
    unittest.main()
