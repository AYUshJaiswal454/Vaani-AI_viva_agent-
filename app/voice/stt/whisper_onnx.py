"""Built-in speech recogniser: OpenAI Whisper exported to ONNX, run with onnxruntime + numpy only.

No PyTorch, no CTranslate2, no GPU: the whole recogniser is ~200 lines and the model files come from the
sherpa-onnx release page (scripts/download_models.py). That makes a fully local, zero-API-cost viva possible on
an ordinary laptop or a small college server.

* Features: Whisper's 80-bin log-mel spectrogram, re-implemented in numpy (25 ms Hann window, 10 ms hop,
  Slaney mel filterbank) so the output matches the reference implementation.
* Decoding: greedy search with the decoder's self-attention KV cache. We keep the per-token log-probabilities
  (average = confidence) and the no-speech probability, so low-confidence transcripts can be flagged for review.
* Subject vocabulary: an optional prompt (e.g. "normalisation, B+ tree, deadlock") is BPE-encoded and passed as
  Whisper's previous-context, which measurably helps technical terms (see docs/TEST_REPORT.md).
* Long answers are split at the quietest point near every 28 s, because the encoder window is 30 s.
"""
from __future__ import annotations

import base64
import queue
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
N_FFT = 400
HOP = 160
MAX_SEGMENT_S = 21.0          # 21 s of speech + 8 s of padding stays inside Whisper's 30 s window
PAD_S = 8.0


# ----------------------------------------------------------------------------- features
@lru_cache(maxsize=4)
def mel_filters(n_mels: int = 80, sr: int = SAMPLE_RATE, n_fft: int = N_FFT) -> np.ndarray:
    """Slaney-style mel filterbank, identical to librosa.filters.mel(sr, n_fft, n_mels) used by Whisper."""
    f_sp = 200.0 / 3
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0

    def hz_to_mel(f):
        f = np.asanyarray(f, dtype=np.float64)
        mel = f / f_sp
        return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep, mel)

    def mel_to_hz(m):
        m = np.asanyarray(m, dtype=np.float64)
        f = f_sp * m
        return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f)

    fftfreqs = np.linspace(0, sr / 2, 1 + n_fft // 2)
    mel_f = mel_to_hz(np.linspace(hz_to_mel(0.0), hz_to_mel(sr / 2), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fftfreqs[None, :]
    weights = np.zeros((n_mels, len(fftfreqs)))
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    enorm = 2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels])
    weights *= enorm[:, None]
    return weights.astype(np.float32)


@lru_cache(maxsize=1)
def _hann() -> np.ndarray:
    n = np.arange(N_FFT)
    return (0.5 - 0.5 * np.cos(2 * np.pi * n / N_FFT)).astype(np.float32)   # periodic Hann, as torch.hann_window


def log_mel(audio: np.ndarray, n_mels: int = 80) -> np.ndarray:
    """(T,) float32 audio at 16 kHz -> (n_mels, frames) normalised log-mel, as whisper.log_mel_spectrogram."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size < N_FFT:
        audio = np.pad(audio, (0, N_FFT - audio.size))
    padded = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")          # torch.stft(center=True)
    n_frames = 1 + (padded.size - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = padded[idx] * _hann()
    spec = np.fft.rfft(frames, n=N_FFT, axis=1)
    power = (spec.real ** 2 + spec.imag ** 2)[:-1].T                          # drop the last frame, (201, frames)
    mel = mel_filters(n_mels) @ power
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


# ----------------------------------------------------------------------------- tokenizer
_PRETOKENIZE = re.compile(r"'s|'t|'re|'ve|'m|'ll|'d| ?[^\W\d_]+| ?\d+| ?[^\s\w]+|\s+(?!\S)|\s+", re.UNICODE)


class Tokenizer:
    """Byte-level BPE using the model's token table (token id == merge rank, as in tiktoken)."""

    def __init__(self, tokens_file: Path) -> None:
        self.id_to_bytes: dict[int, bytes] = {}
        self.bytes_to_id: dict[bytes, int] = {}
        for line in tokens_file.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                b = base64.b64decode(parts[0])
            except ValueError:
                continue
            i = int(parts[1])
            self.id_to_bytes[i] = b
            self.bytes_to_id.setdefault(b, i)

    def decode(self, ids: list[int]) -> str:
        return b"".join(self.id_to_bytes.get(i, b"") for i in ids).decode("utf-8", errors="ignore")

    def _bpe(self, piece: bytes) -> list[int]:
        parts = [bytes([c]) for c in piece]
        while len(parts) > 1:
            best, best_rank = -1, None
            for i in range(len(parts) - 1):
                rank = self.bytes_to_id.get(parts[i] + parts[i + 1])
                if rank is not None and (best_rank is None or rank < best_rank):
                    best, best_rank = i, rank
            if best < 0:
                break
            parts[best:best + 2] = [parts[best] + parts[best + 1]]
        return [self.bytes_to_id[p] for p in parts if p in self.bytes_to_id]

    def encode(self, text: str) -> list[int]:
        out: list[int] = []
        for piece in _PRETOKENIZE.findall(text):
            out.extend(self._bpe(piece.encode("utf-8")))
        return out


# ----------------------------------------------------------------------------- model
@dataclass
class Transcript:
    text: str
    avg_logprob: float
    no_speech_prob: float
    language: str
    duration_s: float
    compute_ms: int

    @property
    def confidence(self) -> float:
        """0..1: exp(average token log-probability), damped when Whisper thinks there was no speech."""
        return float(max(0.0, min(1.0, np.exp(self.avg_logprob) * (1.0 - 0.5 * self.no_speech_prob))))


MAX_TOKENS_PER_S = 7.0


def _loop_period(out: list[int], max_period: int = 40) -> int:
    """Length of a repeated phrase at the end of `out`: a short phrase three times in a row, or a long one (8+ tokens,
    which people do not repeat word for word) twice. 0 if none."""
    n = len(out)
    for p in range(2, min(max_period, n // 2) + 1):
        if out[n - p:] == out[n - 2 * p:n - p] and (p >= 8 or (n >= 3 * p and out[n - p:] == out[n - 3 * p:n - 2 * p])):
            return p
    return 0


class Cancelled(Exception):
    """Raised inside the decoder loop when the caller no longer needs the result (the student kept talking)."""


def _log_softmax(x: np.ndarray) -> np.ndarray:
    m = x.max()
    return x - m - np.log(np.exp(x - m).sum())


class WhisperOnnx:
    def __init__(self, model_dir: Path, name: str, threads: int = 2, int8: bool = True) -> None:
        import onnxruntime as ort

        suffix = ".int8.onnx" if int8 and (model_dir / f"{name}-encoder.int8.onnx").exists() else ".onnx"
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = max(1, threads)
        opts.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(str(model_dir / f"{name}-encoder{suffix}"), sess_options=opts, providers=providers)
        self.decoder = ort.InferenceSession(str(model_dir / f"{name}-decoder{suffix}"), sess_options=opts, providers=providers)
        self.tokenizer = Tokenizer(model_dir / f"{name}-tokens.txt")
        self.name = name
        meta = self.encoder.get_modelmeta().custom_metadata_map
        self.n_text_layer = int(meta["n_text_layer"])
        self.n_text_ctx = int(meta["n_text_ctx"])
        self.n_text_state = int(meta["n_text_state"])
        self.n_mels = int(meta["n_mels"])
        self.sot = int(meta["sot"])
        self.eot = int(meta["eot"])
        self.tok_translate = int(meta["translate"])
        self.tok_transcribe = int(meta["transcribe"])
        self.no_timestamps = int(meta["no_timestamps"])
        self.no_speech = int(meta["no_speech"])
        self.blank = int(meta["blank_id"])
        self.sot_prev = self.tok_transcribe + 2            # <|startofprev|>, fixed offset in both vocabularies
        self.sot_sequence = list(map(int, meta["sot_sequence"].split(",")))
        self.lang2id = dict(zip(meta["all_language_codes"].split(","), map(int, meta["all_language_tokens"].split(","))))
        self.id2lang = {v: k for k, v in self.lang2id.items()}
        self.is_multilingual = int(meta["is_multilingual"]) == 1
        self._in_enc = self.encoder.get_inputs()[0].name
        self._dec_in = [i.name for i in self.decoder.get_inputs()]
        self._dec_out = [o.name for o in self.decoder.get_outputs()][:3]
        self._suppress = np.array(sorted({self.no_timestamps, self.sot, self.no_speech, self.tok_translate, self.sot_prev,
                                          self.tok_transcribe, self.sot_prev - 1, *self.lang2id.values()}), dtype=np.int64)
        self.lock = threading.Lock()   # one ORT run per model at a time keeps latency predictable under load

    # -------------------------------------------------------------- helpers
    def _features(self, audio: np.ndarray) -> np.ndarray:
        # 8 s of real silence after the speech lets the decoder see where the speech ends (so it emits <|endoftext|>)
        # at a fraction of the cost of Whisper's usual 30 s window. Measured on 30 NPTEL clips: same WER as 15 s of
        # padding and 15% faster; 4 s or less breaks end-of-text detection.
        audio = np.concatenate([audio.astype(np.float32), np.zeros(int(SAMPLE_RATE * PAD_S), np.float32)])
        mel = log_mel(audio, self.n_mels).T                                       # (T, n_mels)
        if mel.shape[0] > 3000:
            mel = np.pad(mel[:2950], ((0, 50), (0, 0)))
        return mel.T[None, :, :].astype(np.float32)

    def _run_decoder(self, tokens, k, v, ck, cv, offset):
        logits, k, v = self.decoder.run(self._dec_out, {
            self._dec_in[0]: tokens, self._dec_in[1]: k, self._dec_in[2]: v,
            self._dec_in[3]: ck, self._dec_in[4]: cv, self._dec_in[5]: offset,
        })
        return logits, k, v

    def _segment(self, audio: np.ndarray, language: str, prompt_ids: list[int], cancel=None) -> tuple[str, list[float], float, str, bool]:
        ck, cv = self.encoder.run(None, {self._in_enc: self._features(audio)})
        sot_seq = list(self.sot_sequence)
        lang = "en"
        if self.is_multilingual:
            if language in self.lang2id:
                lang = language
            else:   # auto-detect
                k = np.zeros((self.n_text_layer, 1, self.n_text_ctx, self.n_text_state), np.float32)
                logits, _, _ = self._run_decoder(np.array([[self.sot]], np.int64), k, k.copy(), ck, cv, np.zeros(1, np.int64))
                lg = logits[0, -1]
                ids = np.array(list(self.lang2id.values()))
                lang = self.id2lang[int(ids[np.argmax(lg[ids])])]
            sot_seq[1] = self.lang2id[lang]
        sot_seq.append(self.no_timestamps)
        prefix = ([self.sot_prev] + prompt_ids[-(self.n_text_ctx // 2 - 1):]) if prompt_ids else []
        tokens = np.array([prefix + sot_seq], dtype=np.int64)
        k = np.zeros((self.n_text_layer, 1, self.n_text_ctx, self.n_text_state), np.float32)
        v = np.zeros_like(k)
        offset = np.zeros(1, dtype=np.int64)
        logits, k, v = self._run_decoder(tokens, k, v, ck, cv, offset)
        offset += tokens.shape[1]
        sot_pos = len(prefix)                                                      # logits right after <|startoftranscript|>
        no_speech_prob = float(np.exp(_log_softmax(logits[0, sot_pos].astype(np.float64))[self.no_speech]))
        cur = logits[0, -1].astype(np.float64)
        cur[self._suppress] = -np.inf
        cur[self.eot] = -np.inf                                                    # at least one real token
        cur[self.blank] = -np.inf
        out: list[int] = []
        logprobs: list[float] = []
        # Nobody says more than ~7 tokens a second: bounding the length by the audio keeps a hallucinating decoder
        # from running for many seconds (Whisper can loop on a phrase, especially with a repetitive prompt).
        max_len = min(self.n_text_ctx - tokens.shape[1] - 2, int(audio.size / SAMPLE_RATE * MAX_TOKENS_PER_S) + 12)
        looped = False
        for _ in range(max(1, max_len)):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            lp = _log_softmax(cur)
            tok = int(np.argmax(lp))
            if tok == self.eot:
                break
            out.append(tok)
            logprobs.append(float(lp[tok]))
            period = _loop_period(out)
            if period:                                                             # the same phrase three times: stop
                out, logprobs, looped = out[:-period], logprobs[:-period], True
                break
            logits, k, v = self._run_decoder(np.array([[tok]], np.int64), k, v, ck, cv, offset)
            offset += 1
            cur = logits[0, -1].astype(np.float64)
            cur[self._suppress] = -np.inf
        return self.tokenizer.decode(out).strip(), logprobs, no_speech_prob, lang, looped or len(out) >= max_len

    # -------------------------------------------------------------- public
    def transcribe(self, audio: np.ndarray, language: str = "en", prompt: str = "", cancel=None) -> Transcript:
        """`cancel`: optional threading.Event; when set, decoding stops within one token and Cancelled is raised."""
        t0 = time.perf_counter()
        audio = np.asarray(audio, dtype=np.float32)
        prompt_ids = self.tokenizer.encode(" " + prompt.strip()) if prompt.strip() else []
        texts, all_lp, ns, lang = [], [], [], language
        with self.lock:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            for seg in split_long(audio):
                if seg.size < SAMPLE_RATE * 0.2:
                    continue
                text, lps, no_speech, lang, looped = self._segment(seg, language, prompt_ids, cancel)
                if looped and prompt_ids:     # a loop is usually prompt-induced: decode once more without the prompt
                    t2, lps2, ns2, lang2, looped2 = self._segment(seg, language, [], cancel)
                    if not looped2 or len(t2) < len(text):
                        text, lps, no_speech, lang = t2, lps2, ns2, lang2
                if text:
                    texts.append(text)
                all_lp.extend(lps)
                ns.append(no_speech)
        text = re.sub(r"\s+", " ", " ".join(texts)).strip()
        return Transcript(text=text, avg_logprob=float(np.mean(all_lp)) if all_lp else -3.0,
                          no_speech_prob=float(np.mean(ns)) if ns else 1.0, language=lang,
                          duration_s=audio.size / SAMPLE_RATE, compute_ms=int((time.perf_counter() - t0) * 1000))


def split_long(audio: np.ndarray, max_s: float = MAX_SEGMENT_S) -> list[np.ndarray]:
    """Split audio longer than the encoder window at the quietest 100 ms near each boundary."""
    max_n = int(max_s * SAMPLE_RATE)
    if audio.size <= max_n:
        return [audio]
    out, start = [], 0
    win = SAMPLE_RATE // 10
    while audio.size - start > max_n:
        lo, hi = start + int(max_n * 0.6), start + max_n
        region = audio[lo:hi]
        n = region.size // win
        energy = (region[: n * win].reshape(n, win) ** 2).mean(axis=1)
        cut = lo + int(np.argmin(energy)) * win + win // 2
        out.append(audio[start:cut])
        start = cut
    out.append(audio[start:])
    return out


_models: dict[str, WhisperOnnx] = {}
_models_lock = threading.Lock()


def find_model_dir(models_root: Path, name: str) -> Path | None:
    for cand in (models_root / f"sherpa-onnx-whisper-{name}", models_root / name, models_root):
        if (cand / f"{name}-encoder.int8.onnx").exists() or (cand / f"{name}-encoder.onnx").exists():
            return cand
    return None


def get_model(models_root: Path, name: str, threads: int = 2) -> WhisperOnnx:
    key = f"{models_root}:{name}"
    with _models_lock:
        if key not in _models:
            d = find_model_dir(models_root, name)
            if d is None:
                raise FileNotFoundError(f"Whisper model '{name}' not found under {models_root}. "
                                        f"Run: python scripts/download_models.py --whisper {name}")
            _models[key] = WhisperOnnx(d, name, threads=threads)
        return _models[key]


class ModelPool:
    """One model instance per recognition worker. A Whisper decoder step is mostly sequential work, so on a small
    CPU N one-thread instances recognise ~1.5x more audio per second than one N-thread instance (measured on 2 vCPU:
    9.4x vs 6.3x real time), at the cost of ~140 ms more per single request, which speculation hides."""

    def __init__(self, models_root: Path, name: str, size: int, threads: int = 1) -> None:
        self.models_root, self.name, self.size, self.threads = models_root, name, max(1, size), max(1, threads)
        self.is_multilingual = not name.endswith(".en")
        self._free: queue.Queue[WhisperOnnx] = queue.Queue()
        self._created = 0
        self._lock = threading.Lock()

    def _new(self) -> WhisperOnnx:
        d = find_model_dir(self.models_root, self.name)
        if d is None:
            raise FileNotFoundError(f"Whisper model '{self.name}' not found under {self.models_root}. "
                                    f"Run: python scripts/download_models.py --whisper {self.name}")
        return WhisperOnnx(d, self.name, threads=self.threads)

    def _get(self) -> WhisperOnnx:
        try:
            return self._free.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._created < self.size:
                m = self._new()
                self._created += 1
                return m
        return self._free.get()

    def transcribe(self, audio: np.ndarray, language: str = "en", prompt: str = "", cancel=None) -> Transcript:
        m = self._get()
        try:
            return m.transcribe(audio, language, prompt, cancel)
        finally:
            self._free.put(m)

    def warm(self) -> None:
        held = [self._get() for _ in range(self.size)]
        for m in held:
            m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
        for m in held:
            self._free.put(m)


_pools: dict[str, ModelPool] = {}


def get_pool(models_root: Path, name: str, size: int, threads: int = 1) -> ModelPool:
    key = f"{models_root}:{name}:{size}:{threads}"
    with _models_lock:
        if key not in _pools:
            if find_model_dir(models_root, name) is None:
                raise FileNotFoundError(f"Whisper model '{name}' not found under {models_root}. "
                                        f"Run: python scripts/download_models.py --whisper {name}")
            _pools[key] = ModelPool(models_root, name, size, threads)
        return _pools[key]
