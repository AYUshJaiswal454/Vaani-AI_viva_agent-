"""An audio bot that takes a real viva over the real WebSocket, exactly like a browser would.

It joins with a roll number and the viva code, accepts consent, then streams 16 kHz PCM in 100 ms chunks in real
time: silence while listening, and synthesised speech (ffmpeg's flite voice) for its answers, chosen by a scripted
persona from the question bank. It plays the examiner's audio for its real duration and reports playback events,
so the server measures latency end to end. Used by the end-to-end tests, the robustness tests, the sample vivas and
the load test (50/100/150 bots at once).

Options simulate trouble: background noise at a given SNR, a muted microphone, and a network drop with rejoin.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    import httpx2 as httpx  # type: ignore

from loadtest.wsclient import WSClient  # noqa: E402

SR = 16000
CHUNK = 1600                     # 100 ms
H = {"X-Requested-With": "vaani"}
AUDIO_CACHE = ROOT / "loadtest" / ".audio_cache"


def synth(text: str, voice: str = "kal16", rate: float = 1.0) -> np.ndarray:
    """Speech for `text` as float32 16 kHz, cached on disk (flite through ffmpeg)."""
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:20]
    path = AUDIO_CACHE / f"{key}.pcm"
    if path.exists():
        return np.frombuffer(path.read_bytes(), "<i2").astype(np.float32) / 32768
    tf = AUDIO_CACHE / f"{key}.txt"
    tf.write_text(text.replace("\n", " "), encoding="utf-8")
    flt = f"atempo={rate}," if rate != 1.0 else ""
    p = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"flite=textfile='{tf}':voice={voice}", "-af", f"{flt}aresample={SR}", "-ac", "1", "-f", "s16le", "pipe:1"],
                       capture_output=True, timeout=60)
    tf.unlink(missing_ok=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode()[-300:])
    path.write_bytes(p.stdout)
    return np.frombuffer(p.stdout, "<i2").astype(np.float32) / 32768


POOL_TEXT = [
    "A transaction is a unit of work that either completes fully or not at all.",
    "The primary key uniquely identifies each row in the table.",
    "Normalisation removes redundancy by splitting tables on functional dependencies.",
    "An index lets the database find rows without scanning the whole table.",
    "Two phase locking acquires all locks before releasing any of them.",
    "The write ahead log records every change before the data page is written.",
    "A deadlock happens when two transactions wait for each other forever.",
    "In my project we stored orders and users in separate tables with a foreign key.",
]
_pool: list[np.ndarray] = []


def speech_like(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """Real speech of about `seconds`, stitched from a small pre-synthesised pool. Used in load tests (--hints),
    where the server's stub recogniser takes the words from the hint, so the bots stay cheap: no synthesis per
    answer, which at 150 bots would otherwise make the client, not the server, the bottleneck."""
    if not _pool:
        _pool.extend(synth(t, "kal16") for t in POOL_TEXT)
    parts, total = [], 0.0
    gap = np.zeros(int(0.25 * SR), np.float32)
    while total < seconds:
        c = _pool[int(rng.integers(len(_pool)))]
        parts += [c, gap]
        total += len(c) / SR + 0.25
    return np.concatenate(parts)[: int(seconds * SR)]


def noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Pink-ish background noise (fan / room hum) with occasional clatter."""
    w = rng.standard_normal(n).astype(np.float32)
    b = np.cumsum(w) * 0.02
    b -= np.convolve(b, np.ones(400) / 400, mode="same")
    x = 0.6 * w + b
    return (x / (np.sqrt(np.mean(x ** 2)) + 1e-9)).astype(np.float32)


@dataclass
class BotResult:
    roll_no: str
    persona: str
    session_id: int | None = None
    ok: bool = False
    end_reason: str = ""
    error: str = ""
    turns: int = 0
    latencies_ms: list[float] = field(default_factory=list)     # last answer audio sent -> first examiner message
    audio_fetch_ms: list[float] = field(default_factory=list)
    says: list[dict] = field(default_factory=list)
    started: float = 0.0
    duration_s: float = 0.0
    reconnects: int = 0


class AudioBot:
    def __init__(self, base: str, code: str, roll_no: str, persona, *, name: str = "", voice: str = "kal16", snr_db: float | None = None,
                 mute_after_s: float | None = None, drop_at_s: float | None = None, drop_for_s: float = 8.0,
                 use_hints: bool = False, bank: dict | None = None, speed: float = 1.0, max_s: float = 960,
                 fetch_audio: bool = True, seed: int = 1) -> None:
        self.base = base.rstrip("/")
        self.code = code
        self.roll = roll_no
        self.name = name or f"Bot {roll_no}"
        self.persona = persona
        if hasattr(persona, "student_name"):          # answer the identity check with this bot's own details
            persona.student_name, persona.roll_no = self.name, roll_no
        self.voice = voice
        self.snr_db = snr_db
        self.mute_after_s = mute_after_s
        self.drop_at_s = drop_at_s
        self.drop_for_s = drop_for_s
        self.use_hints = use_hints
        self.bank = {q["code"]: q for q in (bank or {}).get("questions", [])}
        self.speed = speed
        self.max_s = max_s
        self.fetch_audio = fetch_audio
        self.rng = np.random.default_rng(seed)
        self.res = BotResult(roll_no, getattr(persona, "name", "persona"))
        self.seq = 0
        self.offset = 0
        self.outbox: list[bytes] = []         # unacknowledged chunks, resent after a reconnect
        self.ws: WSClient | None = None
        self.token = ""
        self.speech: np.ndarray | None = None
        self.speech_pos = 0
        self.last_answer_sent: float | None = None
        self.waiting_reply = False
        self.playing_until = 0.0
        self.play_queue: list[dict] = []
        self.ended = False
        self.say_pending: dict | None = None

    # ------------------------------------------------------------------ http
    async def join(self, http: httpx.AsyncClient) -> None:
        r = await http.post(self.base + "/api/auth/join", json={"code": self.code, "roll_no": self.roll, "name": self.name}, headers=H)
        if r.status_code != 200:
            raise RuntimeError(f"join {r.status_code}: {r.text[:200]}")
        body = r.json()
        self.token = body["token"]
        self.res.session_id = body["session_id"]
        r = await http.post(self.base + "/api/student/consent", json={"accept": True, "client": {"agent": "audio-bot"}},
                            headers={**H, "authorization": f"Bearer {self.token}"})
        if r.status_code != 200:
            raise RuntimeError(f"consent {r.status_code}: {r.text[:200]}")

    async def connect(self) -> None:
        ws_base = self.base.replace("http://", "ws://").replace("https://", "wss://")
        self.ws = WSClient()
        await self.ws.connect(f"{ws_base}/ws/session/{self.res.session_id}?token={self.token}")

    # ------------------------------------------------------------------ audio out
    def next_chunk(self, now: float) -> bytes:
        if self.speech is not None and self.speech_pos < len(self.speech):
            x = self.speech[self.speech_pos:self.speech_pos + CHUNK]
            self.speech_pos += CHUNK
            if len(x) < CHUNK:
                x = np.pad(x, (0, CHUNK - len(x)))
            if self.speech_pos >= len(self.speech):
                self.speech = None
                self.last_answer_sent = now
                self.waiting_reply = True
        else:
            x = np.zeros(CHUNK, np.float32)
        if self.snr_db is not None:
            sig = 0.05
            x = x + noise(CHUNK, self.rng) * sig * (10 ** (-self.snr_db / 20)) * 4
        if self.mute_after_s is not None and now - self.res.started > self.mute_after_s:
            x = np.zeros(CHUNK, np.float32)
        pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        self.seq += 1
        frame = b"\x01" + struct.pack(">II", self.seq, self.offset) + pcm
        self.offset += CHUNK
        return frame

    async def speak(self, text: str) -> None:
        if self.use_hints and self.ws:
            await self.ws.send_json({"type": "bot_hint", "text": text})
        if self.use_hints:
            self.speech = speech_like(max(1.5, len(text.split()) / 2.4), self.rng)
        else:
            self.speech = synth(text, self.voice, random.uniform(0.95, 1.1))
        self.speech_pos = 0
        self.res.turns += 1

    # ------------------------------------------------------------------ main loop
    async def run(self, http: httpx.AsyncClient) -> BotResult:
        try:
            await self.join(http)
            await self.connect()
            hello = json.loads(await self.ws.recv(timeout=15))
            if hello.get("type") != "hello":
                raise RuntimeError(f"unexpected first message: {hello}")
            await self.ws.send_json({"type": "start", "client": {"agent": "audio-bot", "persona": self.res.persona}})
            self.res.started = time.monotonic()
            await self.loop(http)
            self.res.ok = self.ended
            if not self.ended and self.ws:
                await self.finish_early()          # time limit of the test: leave the way a student would
        except Exception as exc:  # noqa: BLE001
            self.res.error = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            self.res.duration_s = round(time.monotonic() - self.res.started, 1) if self.res.started else 0.0
            if self.ws:
                await self.ws.close()
        return self.res

    async def finish_early(self) -> None:
        """Press "end viva" (end_early) and play the closing line, so the server records a normal ending."""
        await self.ws.send_json({"type": "end_early"})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not self.ended:
            try:
                raw = await self.ws.recv(timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "say":
                for ev in ("start", "end"):
                    await self.ws.send_json({"type": "playback", "event": ev, "utt": msg["utt"], "t": time.time() * 1000})
            elif msg.get("type") == "end":
                self.ended = True
                self.res.end_reason = msg.get("reason", "")

    async def loop(self, http: httpx.AsyncClient) -> None:
        tick = 0.1 / self.speed
        next_send = time.monotonic()
        dropped = False
        while not self.ended and time.monotonic() - self.res.started < self.max_s:
            now = time.monotonic()
            if self.drop_at_s and not dropped and now - self.res.started > self.drop_at_s:
                dropped = True
                await self.ws.close()
                silent_chunks = int(self.drop_for_s / 0.1)
                await asyncio.sleep(self.drop_for_s)
                for _ in range(silent_chunks):           # the browser keeps capturing while offline
                    self.outbox.append(self.next_chunk(time.monotonic()))
                await self.connect()
                self.res.reconnects += 1
                hello = json.loads(await self.ws.recv(timeout=15))
                acked = hello.get("last_seq", -1)
                for fr in self.outbox[-int(5 / 0.1):]:    # resend only the last 5 s (as the browser does)
                    if struct.unpack(">I", fr[1:5])[0] > acked:
                        await self.ws.send_bytes(fr)
                self.outbox.clear()
                next_send = time.monotonic()
                continue
            if now >= next_send:
                fr = self.next_chunk(now)
                await self.ws.send_bytes(fr)
                next_send += tick
            # playback bookkeeping
            if self.say_pending and now >= self.playing_until:
                msg = self.say_pending
                self.say_pending = None
                await self.ws.send_json({"type": "playback", "event": "end", "utt": msg["utt"], "t": time.time() * 1000})
                if msg.get("expect") and not msg.get("more"):
                    await self.answer(msg)
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=max(0.001, next_send - time.monotonic()))
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            await self.on_message(msg, http)

    async def on_message(self, msg: dict, http: httpx.AsyncClient) -> None:
        t = msg.get("type")
        if t == "say":
            now = time.monotonic()
            if self.waiting_reply and self.last_answer_sent:
                self.res.latencies_ms.append((now - self.last_answer_sent) * 1000)
                self.waiting_reply = False
            self.res.says.append({"kind": msg.get("kind"), "code": msg.get("code"), "text": " ".join(s["text"] for s in msg["segments"]),
                                  "t": round(now - self.res.started, 1)})
            dur = 0.0
            for s in msg["segments"]:
                if s.get("audio") and self.fetch_audio:
                    t0 = time.monotonic()
                    r = await http.get(self.base + s["audio"], headers={"authorization": f"Bearer {self.token}"})
                    self.res.audio_fetch_ms.append((time.monotonic() - t0) * 1000)
                    if r.status_code != 200:
                        raise RuntimeError(f"audio {r.status_code}")
                dur += s.get("duration") or len(s["text"]) / 15.0
            await self.ws.send_json({"type": "playback", "event": "start", "utt": msg["utt"], "t": time.time() * 1000})
            self.playing_until = time.monotonic() + dur / self.speed
            if msg.get("more"):
                self.playing_until += 0.0
            self.say_pending = msg
        elif t == "stop_audio":
            self.playing_until = time.monotonic()
        elif t == "end":
            self.ended = True
            self.res.end_reason = msg.get("reason", "")

    async def answer(self, msg: dict) -> None:
        q = self.bank.get(msg.get("code") or "")
        say = {"kind": msg.get("kind"), "segments": msg["segments"], "code": msg.get("code")}
        resp = self.persona.respond(say, q if msg.get("kind") not in ("wrapup",) and q else None)
        if resp is None:
            return
        text, _ = resp
        await asyncio.sleep(random.uniform(0.4, 1.2) / self.speed)
        await self.speak(text)
