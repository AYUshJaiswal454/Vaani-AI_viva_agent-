"""Voice activity detection for a live microphone stream: Silero VAD behind a cheap energy gate.

Silero (an ONNX neural VAD, ~0.25 ms per 32 ms window on one CPU core) rejects background noise, fans, traffic and
keyboard clicks far better than an energy threshold. To keep the cost per viva tiny, it runs only when a frame is
louder than the adaptive noise floor; silent frames cost almost nothing. If the model file is missing, a pure
energy detector is used instead.

The stream turns into events: speech_start, speech_end (after `endpoint_ms` of silence), with the sample offsets,
so the caller can cut out exactly the utterance (plus 300 ms of pre-roll) for speech recognition.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

WIN = 512                    # samples per Silero window at 16 kHz (32 ms)
SR = 16000

_model = None
_model_lock = threading.Lock()


def _load_model(models_dir: Path):
    global _model
    with _model_lock:
        if _model is None:
            path = None
            for cand in ("silero_vad.onnx", "silero_vad_v5.onnx"):
                if (models_dir / cand).exists():
                    path = models_dir / cand
                    break
            if path is None:
                _model = False
                return _model
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            opts.log_severity_level = 3
            sess = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
            names = [i.name for i in sess.get_inputs()]
            _model = (sess, "v5" if "state" in names else "v4")
    return _model


def model_available(models_dir: Path) -> bool:
    return bool(_load_model(models_dir))


@dataclass
class VadEvent:
    kind: str           # speech_start | speech_end
    sample: int         # position in the stream (16 kHz samples since the stream origin)
    prob: float = 0.0


@dataclass
class StreamVAD:
    models_dir: Path
    threshold: float = 0.5
    endpoint_ms: int = 900
    min_speech_ms: int = 250
    origin: int = 0                      # stream position of the first sample fed in
    pos: int = field(init=False, default=0)
    in_speech: bool = field(init=False, default=False)
    speech_start: int = field(init=False, default=0)
    last_voice: int = field(init=False, default=0)
    noise_db: float = field(init=False, default=-60.0)
    level_db: float = field(init=False, default=-120.0)
    peak_db: float = field(init=False, default=-120.0)
    silero_runs: int = field(init=False, default=0)
    frames: int = field(init=False, default=0)
    _buf: np.ndarray = field(init=False, default_factory=lambda: np.zeros(0, np.float32))
    _state: tuple = field(init=False, default=())
    _voiced_run: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.pos = self.origin
        self.last_voice = self.origin
        m = _load_model(self.models_dir)
        self.model = m if m else None
        self.reset_state()

    def reset_state(self) -> None:
        if self.model and self.model[1] == "v5":
            self._state = (np.zeros((2, 1, 128), np.float32),)
        else:
            self._state = (np.zeros((2, 1, 64), np.float32), np.zeros((2, 1, 64), np.float32))

    def _prob(self, x: np.ndarray, db: float) -> float:
        if self.model is None:
            return 1.0 if db > max(self.noise_db + 12, -45) else 0.0
        sess, kind = self.model
        self.silero_runs += 1
        if kind == "v5":
            p, st = sess.run(None, {"input": x[None, :], "state": self._state[0], "sr": np.array(SR, np.int64)})
            self._state = (st,)
        else:
            p, h, c = sess.run(None, {"x": x[None, :], "h": self._state[0], "c": self._state[1]})
            self._state = (h, c)
        return float(p.reshape(-1)[0])

    def feed(self, pcm_f32: np.ndarray, endpoint_ms: int | None = None) -> list[VadEvent]:
        """Feed audio; returns events. `endpoint_ms` may be raised on the fly (e.g. the student said "let me think")."""
        ep = int((endpoint_ms or self.endpoint_ms) * SR / 1000)
        self._buf = np.concatenate([self._buf, pcm_f32.astype(np.float32)])
        events: list[VadEvent] = []
        while self._buf.size >= WIN:
            x, self._buf = self._buf[:WIN], self._buf[WIN:]
            self.frames += 1
            rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)
            db = 20 * np.log10(rms)
            self.level_db = 0.8 * self.level_db + 0.2 * db if self.level_db > -119 else db
            self.peak_db = max(self.peak_db - 0.5, db)
            gate = db > self.noise_db + 6 or self.in_speech
            prob = self._prob(x, db) if gate and db > -65 else 0.0
            if not gate or prob < 0.2:
                # adaptive noise floor: follows quiet frames quickly, loud frames slowly
                self.noise_db = 0.95 * self.noise_db + 0.05 * db if db < self.noise_db + 3 else self.noise_db + 0.02
                self.noise_db = min(max(self.noise_db, -90.0), -25.0)
            end_of_frame = self.pos + WIN
            if prob >= self.threshold:
                self._voiced_run += 1
                self.last_voice = end_of_frame
                if not self.in_speech and self._voiced_run * WIN >= self.min_speech_ms * SR / 1000 * 0.5:
                    self.in_speech = True
                    self.speech_start = max(self.origin, self.pos - (self._voiced_run - 1) * WIN)
                    events.append(VadEvent("speech_start", self.speech_start, prob))
            else:
                self._voiced_run = 0 if prob < self.threshold - 0.15 else self._voiced_run
                if self.in_speech and end_of_frame - self.last_voice >= ep:
                    self.in_speech = False
                    if self.last_voice - self.speech_start >= self.min_speech_ms * SR / 1000:
                        events.append(VadEvent("speech_end", self.last_voice, prob))
                    else:
                        events.append(VadEvent("speech_discard", self.last_voice, prob))
                    self.reset_state()
            self.pos = end_of_frame
        return events

    def force_end(self) -> VadEvent | None:
        if self.in_speech:
            self.in_speech = False
            self.reset_state()
            return VadEvent("speech_end", max(self.last_voice, self.pos), 0.0)
        return None

    @property
    def silence_ms(self) -> int:
        return int((self.pos - self.last_voice) * 1000 / SR)
