"""Audio helpers and the incremental, encrypted recording store.

The student's microphone arrives as 16 kHz mono PCM16 in ~100 ms chunks. Every chunk is appended to the session's
recording immediately (so a crash or a dropped connection loses at most the chunk in flight), optionally encrypted
chunk-by-chunk with Fernet. Silence is inserted for gaps (network drops), so sample n of the file is always time
n / 16000 seconds after the viva started: transcript timestamps and the replay line up exactly.
"""
from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np

from ..config import get_settings
from ..security import decrypt_bytes, encrypt_bytes

SR = 16000
BYTES_PER_S = SR * 2
ENC_MAGIC = b"VAANIENC1\n"


def pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def wav_bytes(pcm: bytes, sr: int = SR) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(data), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    x = np.frombuffer(frames, dtype={1: np.int8, 2: "<i2", 4: "<i4"}[sw]).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    x /= {1: 128.0, 2: 32768.0, 4: 2147483648.0}[sw]
    if sr != SR:
        x = resample(x, sr, SR)
    return float_to_pcm(x), SR


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or x.size == 0:
        return x
    try:
        from math import gcd

        from scipy.signal import resample_poly   # optional: best quality

        g = gcd(sr_in, sr_out)
        return resample_poly(x, sr_out // g, sr_in // g).astype(np.float32)
    except ImportError:
        pass
    x = x.astype(np.float32)
    if sr_out < sr_in:                            # windowed-sinc low-pass before decimating, to avoid aliasing
        fc = 0.45 * sr_out / sr_in
        n = np.arange(-48, 49)
        h = 2 * fc * np.sinc(2 * fc * n) * np.hanning(len(n))
        x = np.convolve(x, (h / h.sum()).astype(np.float32), mode="same")
    t_out = np.arange(int(round(x.size * sr_out / sr_in))) * (sr_in / sr_out)
    return np.interp(t_out, np.arange(x.size), x).astype(np.float32)


def rms_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -120.0
    r = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    return 20 * np.log10(max(r, 1e-6))


def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def decode_to_pcm(data: bytes) -> bytes:
    """Any audio file (mp3/ogg/wav) -> 16 kHz mono PCM16 using ffmpeg (WAV handled natively)."""
    if data[:4] == b"RIFF":
        return read_wav(data)[0]
    ff = ffmpeg_bin()
    if not ff:
        raise RuntimeError("ffmpeg is needed to decode compressed audio")
    p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(SR),
                        "pipe:1"], input=data, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="ignore")[-300:])
    return p.stdout


def encode_pcm(pcm: bytes, fmt: str = "ogg", bitrate: str = "32k") -> tuple[bytes, str]:
    """16 kHz PCM16 -> Opus-in-Ogg (small, seekable in browsers) or MP3; falls back to WAV without ffmpeg."""
    ff = ffmpeg_bin()
    if not ff:
        return wav_bytes(pcm), "audio/wav"
    codec = ["-c:a", "libopus", "-b:a", bitrate, "-f", "ogg"] if fmt == "ogg" else ["-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3"]
    p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                        *codec, "pipe:1"], input=pcm, capture_output=True, timeout=300)
    if p.returncode != 0 or not p.stdout:
        return wav_bytes(pcm), "audio/wav"
    return p.stdout, "audio/ogg" if fmt == "ogg" else "audio/mpeg"


def waveform_peaks(pcm: bytes, buckets: int = 1200) -> list[float]:
    x = np.abs(pcm_to_float(pcm))
    if x.size == 0:
        return []
    n = max(1, x.size // buckets)
    m = x[: (x.size // n) * n].reshape(-1, n).max(axis=1)
    peak = float(m.max()) or 1.0
    return [round(float(v) / peak, 3) for v in m]


# ----------------------------------------------------------------------------- encrypted files
def write_secure(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if get_settings().encrypt_audio:
        tmp.write_bytes(ENC_MAGIC + encrypt_bytes(data))
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)


def read_secure(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw.startswith(ENC_MAGIC):
        return decrypt_bytes(raw[len(ENC_MAGIC):])
    return raw


class Recording:
    """Append-only student track for one session: data/sessions/<id>/student.pcm (framed + encrypted if enabled)."""

    _locks: dict[str, threading.Lock] = {}
    _pending: dict[str, bytearray] = {}      # audio not yet written, shared by every instance for the same file
    FLUSH_BYTES = SR                          # write (and encrypt) in half-second blocks: 5x fewer writes than per frame

    def __init__(self, session_id: int) -> None:
        self.dir = get_settings().sessions_dir / str(session_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "student.pcm"
        self.key = str(self.path)
        self.encrypted = get_settings().encrypt_audio
        self.lock = Recording._locks.setdefault(self.key, threading.Lock())
        self._samples: int | None = None

    def _frame(self, pcm: bytes) -> bytes:
        if not self.encrypted:
            return pcm
        token = encrypt_bytes(pcm)
        return struct.pack(">I", len(token)) + token

    def _flush_unlocked(self) -> None:
        buf = Recording._pending.get(self.key)
        if not buf:
            return
        with open(self.path, "ab") as fh:
            if fh.tell() == 0 and self.encrypted:
                fh.write(ENC_MAGIC)
            fh.write(self._frame(bytes(buf)))
        buf.clear()

    def flush(self) -> None:
        """Write any buffered audio now (end of the viva, disconnect, before reading)."""
        with self.lock:
            self._flush_unlocked()

    def append(self, pcm: bytes) -> int:
        """Append PCM16 and return the total number of samples now stored (written or buffered)."""
        with self.lock:
            if self._samples is None:
                self._samples = self.samples_unlocked()
            buf = Recording._pending.setdefault(self.key, bytearray())
            buf += pcm
            if len(buf) >= self.FLUSH_BYTES:
                self._flush_unlocked()
            self._samples += len(pcm) // 2
            return self._samples

    def pad_to(self, samples: int) -> int:
        """Insert silence so the next chunk lands at `samples` (keeps the recording aligned with the viva clock)."""
        cur = self.samples()
        gap = samples - cur
        if gap > SR // 4:            # ignore jitter under 250 ms
            remaining = gap
            while remaining > 0:
                n = min(remaining, SR * 30)
                self.append(b"\x00\x00" * n)
                remaining -= n
        return self.samples()

    def samples_unlocked(self) -> int:
        pending = len(Recording._pending.get(self.key, b"")) // 2
        if not self.path.exists():
            return pending
        if not self.encrypted:
            return self.path.stat().st_size // 2 + pending
        return len(self.read_unlocked()) // 2

    def samples(self) -> int:
        if self._samples is not None:
            return self._samples
        with self.lock:
            self._samples = self.samples_unlocked()
            return self._samples

    def read_unlocked(self) -> bytes:
        self._flush_unlocked()
        if not self.path.exists():
            return b""
        raw = self.path.read_bytes()
        if not raw.startswith(ENC_MAGIC):
            return raw
        out = bytearray()
        i = len(ENC_MAGIC)
        while i + 4 <= len(raw):
            (n,) = struct.unpack(">I", raw[i:i + 4])
            token = raw[i + 4:i + 4 + n]
            if len(token) < n:
                break            # torn final frame from a crash: everything before it is intact
            out += decrypt_bytes(token)
            i += 4 + n
        return bytes(out)

    def read(self) -> bytes:
        with self.lock:
            return self.read_unlocked()

    def slice(self, t0: float, t1: float) -> bytes:
        pcm = self.read()
        return pcm[int(t0 * SR) * 2:int(t1 * SR) * 2]
