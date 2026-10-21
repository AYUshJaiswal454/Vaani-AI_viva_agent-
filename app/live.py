r"""The live viva: one WebSocket per student, streaming microphone audio in and examiner speech out.

Pipeline for every answer:

    mic (browser, 16 kHz PCM, 100 ms chunks) --ws--> recording (appended + encrypted at once)
                                                 \-> Silero VAD -> end of speech (900 ms of silence)
                                                        -> speech-to-text (started speculatively at the endpoint)
                                                        -> examiner engine (deterministic, < 5 ms)
                                                        -> cached examiner audio (pre-synthesised) --ws--> browser

Robustness built in:
* Every audio chunk carries a sequence number; after a network drop the browser resends what the server has not
  acknowledged, and the recording is padded with silence for the gap so it stays aligned with the clock.
* All state that matters (examiner state machine, transcript, recording) is persisted as it happens, so a student
  who reconnects (to any server process) resumes at the same question; the clock pauses for up to 2 minutes of
  network trouble in total, and the examiner repeats the interrupted question.
* If a student starts talking while the examiner is speaking, the examiner stops (barge-in).
* "I'm done" ends an answer at once; "let me think" is recognised and the examiner waits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import struct
import threading
import time
from collections import deque

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from .config import get_settings
from .db import get_db, now_iso
from .engine.examiner import HARD_STOP_S, Examiner
from .engine.text import looks_incomplete
from .engine.variants import phrase_followup
from .llm.providers import resolve
from .security import CSRF_VALUE, SESSION_COOKIE, decode_session_token
from .voice import stt as stt_mod
from .voice import tts as tts_mod
from .voice.audio import SR, Recording, pcm_to_float
from .voice.vad import StreamVAD
from .vivas import examiner_context, get_session, get_viva, question_prompt, subject_prompt

log = logging.getLogger("vaani.live")
FRAME_AUDIO = 1
PREROLL_S = 0.3
SPECULATE_MS = 350        # start recognising the tail after this much silence (the endpoint is at 900 ms)
RING_S = 110
ACTIVE: dict[int, "LiveSession"] = {}


def _origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True                                   # non-browser clients (load-test bots) send no Origin
    host = ws.headers.get("host", "")
    return origin.split("://", 1)[-1].rstrip("/") == host or origin.rstrip("/") == get_settings().base_url.rstrip("/")


class Utterance:
    """One spoken answer. Recognition runs in chunks while the student is still talking (cut at natural pauses),
    and the final tail is recognised speculatively as soon as the student pauses, so that by the time the pause is
    long enough to count as the end of the answer, most of the text is already known."""

    def __init__(self, start: int) -> None:
        self.start = start
        self.committed = max(0, start - int(PREROLL_S * SR))   # audio before this sample is already transcribed
        self.texts: list[str] = []
        self.confs: list[tuple[float | None, float]] = []
        self.stt_ms: list[int] = []
        self.chunk_task: asyncio.Task | None = None
        self.chunk_end: int | None = None
        self.spec: tuple[int, int, asyncio.Task, threading.Event] | None = None   # (end sample, start sample, task, cancel)
        self.flow: object | None = None
        self.end: int | None = None
        self.browser_from = 0


class LiveSession:
    def __init__(self, ws: WebSocket, session_id: int) -> None:
        self.ws = ws
        self.sid = session_id
        self.settings = get_settings()
        self.token = secrets.token_hex(8)
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.last_seq = -1
        self.ring = bytearray()
        self.ring_start = 0                      # recording sample index of ring[0]
        self.utt: Utterance | None = None         # the answer being spoken right now
        self.finalizing = False
        self.browser_finals: list[str] = []
        self.browser_interim = ""
        self.hints: deque[str] = deque()
        self.client_playing = False
        self.play_started_wall: float | None = None
        self.last_reply: dict | None = None      # latency bookkeeping for the reply in flight
        self.clock_offset = 0.0                  # server_ms - client_ms (from ping/pong)
        self.scratchpad = ""
        self.quiet_since: float | None = None
        self.mic_warned = False
        self.tasks: list[asyncio.Task] = []
        self.utt_counter = 0
        self.trace: deque[tuple] = deque(maxlen=400)   # recent turn-taking events, saved with the session for debugging

    # ------------------------------------------------------------------ setup
    async def run(self) -> None:
        ws = self.ws
        user = self._auth()
        if not _origin_ok(ws) or not user:
            await ws.close(code=4401)
            return
        sess = get_session(self.sid)
        if not sess or (user["role"] == "student" and sess["student_id"] != user["id"]):
            await ws.close(code=4403)
            return
        if not (sess.get("consent") or {}).get("accepted_at"):
            await ws.accept()
            await self.send({"type": "error", "code": "consent_required", "message": "Please accept the consent first."})
            await ws.close(code=4403)
            return
        if sess["status"] in ("ended", "scoring", "scored", "reviewed", "abandoned"):
            await ws.accept()
            await self.send({"type": "end", "reason": "already_finished"})
            await ws.close()
            return
        await ws.accept()
        old = ACTIVE.get(self.sid)
        if old is not None and old is not self:
            await old.shutdown("replaced by a new connection")
        ACTIVE[self.sid] = self
        db = get_db()
        db.update("sessions", self.sid, {"conn_token": self.token, "last_seen_epoch": time.time()})
        self.viva = get_viva(sess["viva_id"])
        self.student = db.fetchone("SELECT * FROM users WHERE id = ?", (sess["student_id"],))
        self.qmap, self.ctx = examiner_context(sess, self.viva, self.student)
        self.sess_settings = sess.get("settings") or {}
        self.stt_engine = self.sess_settings.get("stt_engine") or self.settings.stt_engine
        self.tts_engine = self.sess_settings.get("tts_engine") or self.settings.tts_engine
        self.language = "auto" if self.sess_settings.get("language") == "hinglish" and self.stt_engine != "sarvam" else \
            ("hi" if self.sess_settings.get("language") == "hinglish" else self.settings.stt_language)
        self.prompt = subject_prompt(sess)
        self.q_prompts = {code: question_prompt(q) for code, q in self.qmap.items()}
        self.examiner = Examiner(sess.get("state") or None, self.qmap, self.ctx)
        self.started_epoch = sess.get("started_epoch")
        self.paused_s = float(sess.get("paused_s") or 0.0)
        self.rec = Recording(self.sid)
        self.seq = int(db.scalar("SELECT COALESCE(MAX(seq), 0) FROM turns WHERE session_id = ?", (self.sid,)) or 0)
        resumed = bool(self.started_epoch)
        if resumed:
            st = self.examiner.st
            gap = time.time() - float(st.get("disconnected_at") or time.time())
            grace_left = max(0.0, self.settings.disconnect_grace_s - float(st.get("grace_used") or 0.0))
            add = min(gap, grace_left)
            st["grace_used"] = float(st.get("grace_used") or 0.0) + add
            st.pop("disconnected_at", None)
            self.paused_s += add
            self.persist(paused_s=self.paused_s)
            self.integrity("rejoin", "info", {"gap_s": round(gap, 1), "clock_paused_s": round(add, 1)})
        self.vad = StreamVAD(self.settings.models_dir, threshold=self.settings.vad_threshold,
                             endpoint_ms=self.settings.endpoint_silence_ms, origin=self.rec.samples())
        self.ring_start = self.rec.samples()
        await self.send({
            "type": "hello", "session_id": self.sid, "resumed": resumed, "student": self.student["name"],
            "subject": self.viva["subject"], "title": self.viva["title"], "duration_s": HARD_STOP_S,
            "phases": self.examiner.phases, "stt_engine": self.stt_engine, "tts_engine": self.tts_engine,
            "language": self.sess_settings.get("language", "en"), "barge_in": self.sess_settings.get("barge_in", True),
            "allow_end_early": self.sess_settings.get("allow_end_early", True),
            "code_round": self.sess_settings.get("code_round", True), "last_seq": self.last_seq,
            "elapsed": round(self.viva_now(), 1) if resumed else 0.0, "server_time": time.time() * 1000,
            "rec_samples": self.rec.samples() if resumed else 0, "rec_elapsed": round(self.rec_now(), 2) if resumed else 0.0,
            "transcript": self.transcript_tail() if resumed else [],
        })
        if resumed and self.examiner.st["mode"] != "ended":
            await self.replay_last()
        self.tasks.append(asyncio.create_task(self.timer_loop()))
        try:
            await self.receive_loop()
        finally:
            await self.on_disconnect()

    def _auth(self) -> dict | None:
        token = self.ws.cookies.get(SESSION_COOKIE) or self.ws.query_params.get("token")
        payload = decode_session_token(token) if token else None
        if not payload:
            return None
        return get_db().fetchone("SELECT * FROM users WHERE id = ?", (int(payload["sub"]),))

    # ------------------------------------------------------------------ clocks
    def rec_now(self) -> float:
        """Seconds since the viva started, on the recording's timeline (wall clock)."""
        return time.time() - self.started_epoch if self.started_epoch else 0.0

    def viva_now(self) -> float:
        """The examiner's clock: wall time minus any (bounded) pause for network drops."""
        return max(0.0, self.rec_now() - self.paused_s) if self.started_epoch else 0.0

    def rec_to_viva(self, t_rec: float) -> float:
        return max(0.0, t_rec - self.paused_s)

    # ------------------------------------------------------------------ io
    async def send(self, msg: dict) -> None:
        if self.closed or self.ws.application_state != WebSocketState.CONNECTED:
            return
        try:
            async with self.send_lock:
                await self.ws.send_text(json.dumps(msg, ensure_ascii=False))
        except Exception:
            self.closed = True

    async def receive_loop(self) -> None:
        while not self.closed:
            try:
                msg = await self.ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await self.on_audio(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except ValueError:
                    continue
                await self.on_message(data)

    async def shutdown(self, reason: str) -> None:
        self.closed = True
        try:
            await self.ws.close(code=4000, reason=reason[:100])
        except Exception:
            pass

    def save_trace(self) -> None:
        try:
            path = get_settings().sessions_dir / str(self.sid) / "trace.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                for ev in self.trace:
                    fh.write(json.dumps(ev, default=str) + "\n")
            self.trace.clear()
        except Exception:  # pragma: no cover
            pass

    async def on_disconnect(self) -> None:
        self.closed = True
        self.save_trace()
        for t in self.tasks:
            t.cancel()
        if ACTIVE.get(self.sid) is self:
            ACTIVE.pop(self.sid, None)
        try:
            sess = get_session(self.sid)
            if sess and sess.get("conn_token") == self.token and self.examiner.st.get("mode") not in ("ended",) and self.started_epoch:
                self.examiner.st["disconnected_at"] = time.time()
                self.persist()
                self.integrity("network_drop", "info", {"at": round(self.rec_now(), 1)})
        except Exception:  # pragma: no cover
            log.exception("disconnect bookkeeping failed")

    # ------------------------------------------------------------------ persistence
    def persist(self, **extra) -> None:
        data = {"state": self.examiner.st, "updated_at": now_iso(), "last_seen_epoch": time.time(), **extra}
        get_db().update("sessions", self.sid, data)

    def add_turn(self, speaker: str, kind: str, text: str, t_start: float, t_end: float | None = None, *, source: str | None = None,
                 code: str | None = None, phase: str | None = None, meta: dict | None = None) -> int:
        self.seq += 1
        get_db().insert("turns", {
            "session_id": self.sid, "seq": self.seq, "speaker": speaker, "kind": kind, "phase": phase, "text": text[:6000],
            "source": source, "question_code": code, "t_start": round(t_start, 2), "t_end": None if t_end is None else round(t_end, 2),
            "meta": meta or {}, "created_at": now_iso(),
        })
        return self.seq

    def integrity(self, kind: str, severity: str, detail: dict | None = None) -> None:
        if not self.sess_settings.get("integrity", True) and kind not in ("network_drop", "rejoin", "injection", "identity_mismatch"):
            return
        get_db().insert("integrity_events", {"session_id": self.sid, "t": round(self.rec_now(), 1), "kind": kind,
                                             "severity": severity, "detail": detail or {}, "created_at": now_iso()})

    def transcript_tail(self, n: int = 12) -> list[dict]:
        rows = get_db().fetchall("SELECT seq, speaker, kind, text, t_start FROM turns WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
                                 (self.sid, n))
        return list(reversed(rows))

    # ------------------------------------------------------------------ client messages
    async def on_message(self, m: dict) -> None:
        kind = m.get("type")
        if kind == "start":
            if not self.started_epoch:
                self.started_epoch = time.time()
                self.rec.pad_to(self.rec.samples())
                self.ring_start = self.rec.samples()
                self.vad = StreamVAD(self.settings.models_dir, threshold=self.settings.vad_threshold,
                                     endpoint_ms=self.settings.endpoint_silence_ms, origin=self.rec.samples())
                get_db().update("sessions", self.sid, {"status": "live", "started_at": now_iso(),
                                                       "started_epoch": self.started_epoch,
                                                       "client": {**(get_session(self.sid).get("client") or {}),
                                                                  **(m.get("client") or {})}})
                await self.execute(self.examiner.start(0.0))
        elif kind == "playback":
            await self.on_playback(m)
        elif kind == "browser_stt":
            text = (m.get("text") or "").strip()
            if m.get("final"):
                if text:
                    self.browser_finals.append(text)
                self.browser_interim = ""
            else:
                self.browser_interim = text
            if text:
                await self.send({"type": "caption", "text": " ".join(self.browser_finals + [self.browser_interim]).strip()})
        elif kind == "done":
            await self.force_endpoint(reason="student pressed done")
        elif kind == "repeat":
            await self.execute(self.examiner.repeat_request(self.viva_now()))
        elif kind == "end_early":
            if self.sess_settings.get("allow_end_early", True):
                await self.execute(self.examiner.end_early(self.viva_now()))
        elif kind == "integrity":
            k = str(m.get("kind") or "")[:40]
            if k in ("tab_hidden", "focus_lost", "paste", "copy", "fullscreen_exit", "devtools"):
                self.integrity(k, "warn" if k in ("tab_hidden", "paste") else "info", {"client": m.get("detail") or {}})
        elif kind == "code":
            self.scratchpad = str(m.get("text") or "")[:20000]
            self.examiner.st["scratchpad"] = self.scratchpad
        elif kind == "ping":
            now_ms = time.time() * 1000
            if m.get("t") is not None:
                self.clock_offset = now_ms - float(m["t"]) - float(m.get("rtt") or 0) / 2
            await self.send({"type": "pong", "t": m.get("t"), "server_time": now_ms, "elapsed": round(self.viva_now(), 2),
                             "ack": self.last_seq})
        elif kind == "mic":
            if m.get("muted") and self.started_epoch and not self.mic_warned:
                self.mic_warned = True
                self.integrity("mic_muted", "info", {"source": "client"})
        elif kind == "bot_hint" and self.settings.allow_test_hooks:
            self.hints.append(str(m.get("text") or ""))

    async def on_playback(self, m: dict) -> None:
        ev = m.get("event")
        if ev == "start":
            self.client_playing = True
            self.play_started_wall = time.time()
            lr = self.last_reply
            if lr and lr.get("utt") == m.get("utt") and lr.get("voice_end_wall") and "client_ms" not in lr:
                client_wall = (float(m.get("t") or 0) + self.clock_offset) / 1000 if m.get("t") else time.time()
                lr["client_ms"] = int(max(0.0, min(client_wall, time.time()) - lr["voice_end_wall"]) * 1000)
                self.record_latency(lr)
        elif ev in ("end", "skipped"):
            self.client_playing = False
            if self.started_epoch:
                await self.execute(self.examiner.playback_done(self.viva_now()))

    # ------------------------------------------------------------------ audio
    async def on_audio(self, data: bytes) -> None:
        """Binary frame: [1 byte type=1][uint32 seq][uint32 sample offset since the viva started][PCM16 LE mono 16 kHz]."""
        if len(data) < 9 or data[0] != FRAME_AUDIO:
            return
        seq, offset = struct.unpack(">II", data[1:9])
        pcm = data[9:]
        if seq <= self.last_seq:
            return                                        # resent after a reconnect and already stored
        self.last_seq = seq
        if not self.started_epoch:
            await self.send({"type": "level", "db": round(float(self._level(pcm)), 1)})
            return
        if self.examiner.st.get("mode") == "ended":
            return
        cur = self.rec.samples()
        if offset > cur:
            gap = offset - cur
            self.rec.pad_to(offset)
            if gap > 3 * SR:
                # long gap (network drop): restart detection at the new position; any half-heard answer is dropped
                self.ring = bytearray()
                self.ring_start = self.rec.samples()
                self.vad = StreamVAD(self.settings.models_dir, threshold=self.settings.vad_threshold,
                                     endpoint_ms=self.settings.endpoint_silence_ms, origin=self.rec.samples())
                self.utt = None
                self.integrity("audio_gap", "info", {"seconds": round(gap / SR, 1)})
            else:
                zeros = b"\x00\x00" * (self.rec.samples() - cur)
                self.ring += zeros
                for ev in self.vad.feed(pcm_to_float(zeros)):
                    await self.on_vad(ev)
        elif offset < cur:
            overlap = cur - offset
            pcm = pcm[overlap * 2:]
            if not pcm:
                return
        self.rec.append(pcm)
        self.ring += pcm
        overflow = len(self.ring) - RING_S * SR * 2
        if overflow > 0:
            overflow -= overflow % 2
            del self.ring[:overflow]
            self.ring_start += overflow // 2
        if self.last_seq % 10 == 0:
            await self.send({"type": "ack", "seq": self.last_seq})
        for ev in self.vad.feed(pcm_to_float(pcm)):
            await self.on_vad(ev)
        await self.check_barge_in()
        self.maybe_chunk()
        self.maybe_speculate()
        self.check_mic()

    def _level(self, pcm: bytes) -> float:
        from .voice.audio import rms_dbfs

        return rms_dbfs(pcm_to_float(pcm))

    @staticmethod
    def sample_to_rec_t(sample: int) -> float:
        return sample / SR             # sample 0 of the recording is the moment the viva started

    def check_mic(self) -> None:
        st = self.examiner.st
        if st.get("mode") != "listening" or self.mic_warned:
            self.quiet_since = None
            return
        if self.vad.level_db < -78:
            self.quiet_since = self.quiet_since or time.time()
            if time.time() - self.quiet_since > 6:
                self.mic_warned = True
                self.integrity("mic_muted", "info", {"level_db": round(self.vad.level_db, 1)})
                asyncio.create_task(self.send({"type": "warning", "code": "mic_silent",
                                               "message": "We can't hear any sound from your microphone. Check that it is on and unmuted."}))
        else:
            self.quiet_since = None

    def note(self, *ev) -> None:
        self.trace.append((round(self.rec_now(), 2), *ev))

    async def on_vad(self, ev) -> None:
        st = self.examiner.st
        self.note("vad", ev.kind, round(ev.sample / SR, 2), st.get("mode"), self.client_playing)
        if ev.kind == "speech_start":
            if st.get("mode") == "speaking" and self.client_playing:
                # Possible barge-in, or the examiner's own voice leaking from speakers into the microphone.
                # Only act once the speech has lasted 600 ms (see check_barge_in).
                if self.sess_settings.get("barge_in", True):
                    self.barge_candidate = ev.sample
                return
            if self.utt is not None and not self.finalizing:
                self.resume(self.utt)                      # the student kept talking: same answer continues
            elif self.utt is None:
                self.utt = Utterance(ev.sample)
                self.utt.browser_from = len(self.browser_finals)
            self.examiner.speech_started(self.viva_now())
            await self.send({"type": "listening", "speaking": True})
        elif ev.kind in ("speech_end", "speech_discard") and getattr(self, "barge_candidate", None) is not None:
            self.barge_candidate = None                    # too short to be a real interruption: ignore
        elif ev.kind == "speech_end" and self.utt is not None:
            self.examiner.speech_stopped(self.viva_now())
            self.utt.end = ev.sample
            self.spawn(self.endpoint_flow(self.utt, time.time() - self.vad.silence_ms / 1000))
        elif ev.kind == "speech_discard":
            if self.utt is not None and self.utt.flow is None and not self.utt.texts and self.utt.chunk_task is None:
                self.utt = None

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.append(t)
        if len(self.tasks) > 64:
            self.tasks = [x for x in self.tasks if not x.done()]
        return t

    async def check_barge_in(self) -> None:
        cand = getattr(self, "barge_candidate", None)
        if cand is None:
            return
        if not self.vad.in_speech:
            self.barge_candidate = None
            return
        if self.vad.pos - cand >= int(0.6 * SR):
            self.barge_candidate = None
            await self.send({"type": "stop_audio", "reason": "barge_in"})
            self.client_playing = False
            self.utt = Utterance(cand)
            self.utt.browser_from = len(self.browser_finals)
            self.examiner.speech_started(self.viva_now())
            self.integrity("barge_in", "info", {})
            await self.send({"type": "listening", "speaking": True})

    # ------------------------------------------------------------------ streaming recognition
    def _slice(self, a: int, b: int) -> bytes:
        a = max(self.ring_start, a)
        b = min(self.ring_start + len(self.ring) // 2, b)
        return bytes(self.ring[(a - self.ring_start) * 2:(b - self.ring_start) * 2]) if b > a else b""

    def _chunkable(self) -> bool:
        return self.stt_engine not in ("browser", "stub")

    async def transcribe_range(self, u: Utterance, a: int, b: int, final: bool,
                               cancel: threading.Event | None = None) -> stt_mod.STTResult:
        pcm = self._slice(a, b + int(0.15 * SR))
        if self.stt_engine == "browser":
            for _ in range(8):                         # give the browser a moment to deliver its final result
                if len(self.browser_finals) > u.browser_from:
                    break
                await asyncio.sleep(0.09)
            text = " ".join(self.browser_finals[u.browser_from:]).strip() or self.browser_interim
            return stt_mod.STTResult(text, None, "browser", "web-speech", 0, len(pcm) / 2 / SR)
        hint = self.hints.popleft() if (self.stt_engine == "stub" and final and self.hints) else None
        if self.stt_engine in ("whisper-onnx", "faster-whisper"):
            # Local Whisper pays ~2 ms of CPU per prompt token on every call, so it gets the key terms of the question
            # being answered plus the last sentence or so, not the whole subject vocabulary (measured: 60 terms add
            # ~370 ms to a 3 s answer with base.en).
            cur = self.examiner.st.get("cur") or {}
            base, context = self.q_prompts.get(cur.get("code"), ""), " ".join(u.texts)[-120:]
        else:
            base, context = self.prompt, " ".join(u.texts)[-240:]
        prompt = ". ".join(x for x in (base, context) if x)
        return await stt_mod.transcribe(pcm, engine=self.stt_engine, language=self.language, prompt=prompt,
                                        session_id=self.sid, hint=hint, cancel=cancel)

    def maybe_chunk(self) -> None:
        """While the student is still talking, transcribe what they have said so far, cutting at a natural pause."""
        u = self.utt
        if u is None or u.chunk_task is not None or u.flow is not None or not self._chunkable() or not self.vad.in_speech:
            return
        if self.vad.silence_ms >= 200 or u.spec is not None:
            return                                          # paused: the speculative tail covers this audio
        span = self.vad.pos - u.committed
        if span < int(5.5 * SR):
            return
        lo = max(u.committed + int(3.5 * SR), self.vad.pos - int(2 * SR))
        hi = self.vad.pos - int(0.1 * SR)
        if hi - lo < SR // 2:
            return
        x = pcm_to_float(self._slice(lo, hi))
        win = SR // 10
        n = len(x) // win
        if n < 3:
            return
        e = (x[: n * win].reshape(n, win) ** 2).mean(axis=1)
        k = int(e.argmin())
        quiet = e[k] < 0.15 * float(e.mean()) or span > 14 * SR
        if not quiet:
            return
        cut = lo + k * win + win // 2
        u.chunk_end = cut
        u.chunk_task = self.spawn(self._run_chunk(u, u.committed, cut))

    async def _commit(self, u: Utterance, res: stt_mod.STTResult, a: int, b: int) -> None:
        if res.error == "cancelled" or not (self.utt is u or u.flow is not None):
            return
        if res.text:
            u.texts.append(res.text)
        u.confs.append((res.confidence, (b - a) / SR))
        u.stt_ms.append(res.compute_ms)
        u.committed = b
        self.note("chunk_done", res.compute_ms)
        await self.send({"type": "caption", "text": " ".join(u.texts), "partial": True})

    async def _run_chunk(self, u: Utterance, a: int, b: int) -> None:
        self.note("chunk_start", a / SR, b / SR)
        try:
            await self._commit(u, await self.transcribe_range(u, a, b, final=False), a, b)
        finally:
            u.chunk_task = None

    async def _adopt(self, u: Utterance, task: asyncio.Task, a: int, b: int) -> None:
        try:
            await self._commit(u, await task, a, b)
        finally:
            u.chunk_task = None

    def resume(self, u: Utterance) -> None:
        """The student paused and then carried on. A pause is a natural boundary, so the speculative recognition of
        the speech before it is kept as a committed chunk instead of being thrown away; the final tail then only
        covers what comes after the pause."""
        u.flow = None
        sp, u.spec = u.spec, None
        if sp is None:
            return
        end, start, task, cancel = sp
        if u.chunk_task is None and start == u.committed and end - start >= SR:
            u.chunk_end = end
            u.chunk_task = self.spawn(self._adopt(u, task, start, end))
            self.note("adopt", start / SR, end / SR)
        else:
            cancel.set()

    def maybe_speculate(self) -> None:
        """The student has paused: start recognising the tail now, before the pause counts as the end of the answer."""
        u = self.utt
        # The VAD stays "in speech" until the endpoint fires, so the pause is measured with silence_ms.
        if u is None or u.flow is not None or not self.vad.in_speech or not self._chunkable():
            return
        if self.vad.silence_ms < SPECULATE_MS:
            return
        end = self.vad.last_voice
        start = u.chunk_end if u.chunk_task is not None and u.chunk_end else u.committed
        if u.spec and u.spec[0] == end and u.spec[1] == start:
            return
        if u.spec:
            u.spec[3].set()                                 # a blip of sound moved the end: the old guess is stale
        if end - start < int(0.2 * SR):
            u.spec = None
            return
        cancel = threading.Event()
        u.spec = (end, start, self.spawn(self.transcribe_range(u, start, end, final=True, cancel=cancel)), cancel)
        self.note("spec", start / SR, end / SR)

    async def endpoint_flow(self, u: Utterance, voice_end_wall: float, cut: bool = False) -> None:
        token = object()
        u.flow = token
        self.note("flow_start", u.start / SR, (u.end or 0) / SR, cut)
        detected = time.time()
        if u.chunk_task is not None:
            try:
                await asyncio.shield(u.chunk_task)
            except Exception:
                pass
        if u.flow is not token:
            self.note("flow_abort", "after chunk")
            return
        end = u.end if u.end is not None else self.vad.pos
        tail = None
        if u.spec and u.spec[0] == end and u.spec[1] == u.committed:
            tail = await u.spec[2]
            self.note("spec_used", tail.compute_ms)
        elif u.spec:
            u.spec[3].set()
        if tail is None or tail.error == "cancelled":
            if u.flow is not token:
                return
            tail = await self.transcribe_range(u, u.committed, end, final=True)
        if u.flow is not token:
            self.note("flow_abort", "after tail")
            return
        parts = [*u.texts, tail.text] if tail.text else list(u.texts)
        text = " ".join(p.strip() for p in parts if p and p.strip())
        u.confs.append((tail.confidence, max(0.01, (end - u.committed) / SR)))
        u.stt_ms.append(tail.compute_ms)
        if not cut and text and looks_incomplete(text):
            # The student stopped mid-sentence ("... because"): give them a little longer before replying.
            await asyncio.sleep(max(0.0, (self.settings.endpoint_incomplete_ms - self.settings.endpoint_silence_ms) / 1000))
            if u.flow is not token:
                self.note("flow_abort", "incomplete wait")
                return
        self.note("finalize", len(text))
        await self.finalize(u, text, tail, voice_end_wall, detected, cut)

    async def force_endpoint(self, reason: str, cut: bool = False) -> None:
        u = self.utt
        self.note("force", reason, u is not None and u.flow is not None)
        if u is None:
            return
        if u.flow is not None and not cut:
            return
        ev = self.vad.force_end()
        u.end = ev.sample if ev else self.ring_start + len(self.ring) // 2
        await self.endpoint_flow(u, time.time(), cut=cut)

    async def finalize(self, u: Utterance, text: str, tail, voice_end_wall: float, detected: float, cut: bool) -> None:
        if self.finalizing:
            return
        self.finalizing = True
        try:
            if self.utt is u:
                self.utt = None
            start, end = u.start, (u.end or u.start)
            dur = (end - start) / SR
            if not text and dur < 1.0 and not cut:
                return                                     # a cough or a click, not an answer
            confs = [(c, d) for c, d in u.confs if c is not None]
            conf = sum(c * d for c, d in confs) / sum(d for _, d in confs) if confs else None
            stt_meta = {**tail.to_meta(), "confidence": None if conf is None else round(conf, 3), "chunks": len(u.confs),
                        "tail_ms": tail.compute_ms, "total_compute_ms": sum(u.stt_ms)}
            t_dec = time.perf_counter()
            actions = self.examiner.answer(text, self.viva_now(), {"cut": cut})
            decide_ms = (time.perf_counter() - t_dec) * 1000
            ans = self.examiner.last_answer or {}
            meta = {"answer": ans, "stt": stt_meta, "duration_s": round(dur, 2)}
            if self.scratchpad and ans.get("phase") == "applied":
                meta["scratchpad"] = self.scratchpad[-4000:]
            self.add_turn("student", "answer", text or "(inaudible)", self.sample_to_rec_t(start), self.sample_to_rec_t(end),
                          code=ans.get("code"), phase=ans.get("phase"), meta=meta)
            self.browser_finals.clear()
            self.browser_interim = ""
            self.last_reply = {"voice_end_wall": voice_end_wall, "detected_wall": detected, "stt_ms": int((time.time() - detected) * 1000),
                               "decide_ms": round(decide_ms, 2), "endpoint_ms": int((detected - voice_end_wall) * 1000),
                               "engine": self.stt_engine}
            await self.execute(actions, reply_to_answer=True)
        finally:
            self.finalizing = False

    # ------------------------------------------------------------------ examiner actions
    async def execute(self, actions: list[dict], reply_to_answer: bool = False) -> None:
        if actions:
            self.note("exec", [a.get("kind") or a["type"] for a in actions])
        for act in actions:
            t = act["type"]
            if t in ("say", "cut"):
                await self.say(act, reply_to_answer and t == "say")
                reply_to_answer = False
                if t == "cut":
                    self.spawn(self.force_endpoint("answer time cap", cut=True))
            elif t == "flag":
                self.integrity(act["kind"], act.get("severity", "warn"), act.get("detail"))
            elif t == "end":
                await self.end(act.get("reason", "completed"))
                return
        self.persist()

    async def say(self, act: dict, is_reply: bool) -> None:
        self.utt_counter += 1
        utt = f"{self.sid}-{self.seq + 1}-{self.utt_counter}"
        segments = list(act["segments"])
        variant_info = None
        t_tts = time.perf_counter()
        out_segments = []
        req = act.get("variant_request")
        llm_task = None
        if req and self.sess_settings.get("live_llm", True) and self.settings.live_llm and not resolve().is_mock:
            llm_task = asyncio.create_task(phrase_followup(req, self.sid, self.ctx.get("aliases")))
        first = True
        for i, seg in enumerate(segments):
            if llm_task is not None and seg["source"] == req["source"]:
                if first is False and out_segments:
                    # play what we have (the acknowledgement) while the model phrases the follow-up
                    await self.send_say(utt, act, out_segments, more=True, is_reply=is_reply, t_tts=t_tts)
                    is_reply = False
                    out_segments = []
                text, variant_info = await llm_task
                if text:
                    seg = {"text": text, "source": "variant:" + req["source"].removeprefix("bank:"), "base": seg["text"]}
            clip = await tts_mod.get_clip(seg["text"], self.tts_engine, self.sid) if self.tts_engine in tts_mod.SERVER_ENGINES else None
            out_segments.append({"text": seg["text"], "source": seg["source"], "audio": tts_mod.clip_url(clip),
                                 "duration": clip["duration_s"] if clip else None, **({"base": seg["base"]} if seg.get("base") else {})})
            first = False
        await self.send_say(utt, act, out_segments, more=False, is_reply=is_reply, t_tts=t_tts, variant=variant_info)

    async def send_say(self, utt: str, act: dict, segs: list[dict], *, more: bool, is_reply: bool, t_tts: float,
                       variant: dict | None = None) -> None:
        tts_ms = int((time.perf_counter() - t_tts) * 1000)
        text = " ".join(s["text"] for s in segs)
        t_rec = self.rec_now()
        meta = {"reason": act.get("reason"), "segments": [{k: s[k] for k in ("text", "source") if k in s} | (
            {"base": s["base"]} if s.get("base") else {}) for s in segs], "utt": utt}
        if act.get("assessment"):
            meta["assessment_of_previous"] = {k: act["assessment"].get(k) for k in ("label", "coverage", "covered", "reasoning")}
        if variant:
            meta["variant"] = variant
        if is_reply and self.last_reply:
            lr = self.last_reply
            lr.update({"utt": utt, "tts_ms": tts_ms, "server_ms": int((time.time() - lr["voice_end_wall"]) * 1000)})
            meta["latency"] = {k: lr[k] for k in ("endpoint_ms", "stt_ms", "decide_ms", "tts_ms", "server_ms") if k in lr}
        kind = "ack" if more else act["kind"]
        self.add_turn("examiner", kind, text, t_rec, source=",".join(s["source"] for s in segs), code=act.get("code"),
                      phase=act.get("phase"), meta=meta)
        if act.get("log_integrity"):
            li = act["log_integrity"]
            self.integrity(li["kind"], li.get("severity", "info"), li.get("detail"))
        self.client_playing = any(s.get("audio") for s in segs) or self.tts_engine == "browser"
        await self.send({"type": "say", "utt": utt, "kind": kind, "segments": segs, "more": more,
                         "expect": act.get("expect", True), "phase": act.get("phase"), "code": act.get("code"),
                         "show_code": act.get("show_code") if self.sess_settings.get("code_round", True) else None,
                         "elapsed": round(self.viva_now(), 1), "tts": self.tts_engine})

    async def replay_last(self) -> None:
        last = self.examiner.st.get("last_say")
        if not last:
            return
        segs = []
        for s in last["segments"]:
            clip = await tts_mod.get_clip(s["text"], self.tts_engine, self.sid) if self.tts_engine in tts_mod.SERVER_ENGINES else None
            segs.append({"text": s["text"], "source": s["source"], "audio": tts_mod.clip_url(clip)})
        self.examiner.st["mode"] = "speaking"
        await self.send({"type": "say", "utt": f"{self.sid}-replay-{int(time.time())}", "kind": "repeat", "segments": segs,
                         "more": False, "expect": True, "phase": self.examiner.st.get("phase"), "code": last.get("code"),
                         "resumed": True, "elapsed": round(self.viva_now(), 1), "tts": self.tts_engine,
                         "show_code": (self.qmap.get(last.get("code") or "", {}) or {}).get("code_snippet") or None})

    def record_latency(self, lr: dict) -> None:
        try:
            sess = get_session(self.sid)
            lat = sess.get("latency") or {}
            samples = lat.get("samples") or []
            samples.append({k: lr.get(k) for k in ("endpoint_ms", "stt_ms", "decide_ms", "tts_ms", "server_ms", "client_ms", "engine")})
            lat["samples"] = samples[-200:]
            get_db().update("sessions", self.sid, {"latency": lat})
        except Exception:  # pragma: no cover
            log.exception("latency write failed")

    async def end(self, reason: str) -> None:
        db = get_db()
        st = self.examiner.st
        st["mode"] = "ended"
        db.update("sessions", self.sid, {"status": "ended", "ended_at": now_iso(), "end_reason": reason, "state": st,
                                         "audio_samples": self.rec.samples(), "updated_at": now_iso()})
        self.save_trace()
        from .tasks import enqueue

        enqueue("finalize_session", {"session_id": self.sid}, dedupe_key=f"finalize:{self.sid}")
        await self.send({"type": "end", "reason": reason, "elapsed": round(self.viva_now(), 1)})
        self.closed = True
        try:
            await self.ws.close()
        except Exception:
            pass

    async def timer_loop(self) -> None:
        last_beat = 0.0
        try:
            while not self.closed:
                await asyncio.sleep(0.25)
                if not self.started_epoch or self.examiner.st.get("mode") == "ended":
                    continue
                busy = self.finalizing or (self.utt is not None and self.utt.flow is not None)
                if not busy:
                    acts = self.examiner.tick(self.viva_now())
                    if acts:
                        await self.execute(acts)
                u = self.utt
                if u is not None and u.flow is None and not self.vad.in_speech and not self.finalizing \
                        and self.vad.silence_ms > self.settings.endpoint_silence_ms + 800:
                    self.spawn(self.force_endpoint("safety endpoint"))
                if time.time() - last_beat > 5:
                    last_beat = time.time()
                    row = get_db().fetchone("SELECT conn_token FROM sessions WHERE id = ?", (self.sid,))
                    if row and row["conn_token"] != self.token:
                        await self.shutdown("session opened elsewhere")
                        return
                    get_db().execute("UPDATE sessions SET last_seen_epoch = ?, audio_samples = ? WHERE id = ?",
                                     (time.time(), self.rec.samples(), self.sid))
        except asyncio.CancelledError:
            return
        except Exception:  # pragma: no cover
            log.exception("timer loop crashed for session %s", self.sid)


async def ws_endpoint(ws: WebSocket) -> None:
    sid = int(ws.path_params["session_id"])
    live = LiveSession(ws, sid)
    try:
        await live.run()
    except Exception:  # pragma: no cover
        log.exception("live session %s crashed", sid)
        try:
            await ws.close(code=1011)
        except Exception:
            pass


def sweep_stale_sessions() -> int:
    """Background: end live sessions whose student never came back (past the hard stop plus the grace period)."""
    db = get_db()
    now = time.time()
    n = 0
    grace = get_settings().disconnect_grace_s
    for s in db.fetchall("SELECT id, started_epoch, paused_s, last_seen_epoch, state FROM sessions WHERE status = 'live'"):
        if s["id"] in ACTIVE:
            continue
        started = s["started_epoch"] or now
        st = s.get("state") or {}
        disc = float(st.get("disconnected_at") or s.get("last_seen_epoch") or now)
        over_time = now - started - float(s["paused_s"] or 0) > HARD_STOP_S + grace
        gone_too_long = now - disc > grace + 60
        if over_time or gone_too_long:
            st["mode"] = "ended"
            db.update("sessions", s["id"], {"status": "ended", "ended_at": now_iso(), "end_reason": "disconnected", "state": st})
            from .tasks import enqueue

            enqueue("finalize_session", {"session_id": s["id"]}, dedupe_key=f"finalize:{s['id']}")
            n += 1
    return n


__all__ = ["ws_endpoint", "sweep_stale_sessions", "ACTIVE", "CSRF_VALUE"]
