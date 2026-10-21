"""Runtime configuration.

Every setting can be overridden with an environment variable (or a `.env` file in the project root). Secrets that
are missing in development are generated once and kept inside DATA_DIR so restarts keep working; in production
they must be provided. API keys are only ever read from the environment: they are never stored in the repository
or the database.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
DATA_FILES = APP_DIR / "data"


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in ('"', "'") and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
        os.environ.setdefault(key, value)


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _persisted_secret(data_dir: Path, name: str, factory) -> str:
    """Return a secret from the environment, or create one atomically and keep it in DATA_DIR."""
    value = env(name)
    if value:
        return value
    path = data_dir / f".{name.lower()}"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        import time

        for _ in range(50):
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
            time.sleep(0.05)
        return path.read_text(encoding="utf-8").strip()
    value = factory()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    return value


def _fernet_key() -> str:
    import base64

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: env("APP_ENV", "development"))
    base_url: str = field(default_factory=lambda: env("BASE_URL", env("RENDER_EXTERNAL_URL", "http://localhost:8000")))
    data_dir: Path = field(default_factory=lambda: Path(env("DATA_DIR", str(ROOT / "data"))).resolve())
    database_url: str = field(default_factory=lambda: env("DATABASE_URL", ""))
    models_dir: Path = field(default_factory=lambda: Path(env("MODELS_DIR", str(ROOT / "models"))).resolve())

    # ---- Language models. The live examiner uses a fast model; the scorer a stronger one, after the viva. ----
    llm_provider: str = field(default_factory=lambda: env("LLM_PROVIDER", ""))   # openai | groq | ollama | mock
    openai_api_key: str = field(default_factory=lambda: env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model_live: str = field(default_factory=lambda: env("OPENAI_MODEL_LIVE", "gpt-6-luna"))
    openai_model_scorer: str = field(default_factory=lambda: env("OPENAI_MODEL_SCORER", "gpt-6.1-sol"))
    openai_reasoning_live: str = field(default_factory=lambda: env("OPENAI_REASONING_EFFORT_LIVE", "minimal"))
    openai_reasoning_scorer: str = field(default_factory=lambda: env("OPENAI_REASONING_EFFORT_SCORER", "medium"))
    groq_api_key: str = field(default_factory=lambda: env("GROQ_API_KEY"))
    groq_base_url: str = field(default_factory=lambda: env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    groq_model_live: str = field(default_factory=lambda: env("GROQ_MODEL_LIVE", "openai/gpt-oss-20b"))
    groq_model_scorer: str = field(default_factory=lambda: env("GROQ_MODEL_SCORER", "openai/gpt-oss-120b"))
    ollama_base_url: str = field(default_factory=lambda: env("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    ollama_model_live: str = field(default_factory=lambda: env("OLLAMA_MODEL_LIVE", "qwen2.5:3b-instruct"))
    ollama_model_scorer: str = field(default_factory=lambda: env("OLLAMA_MODEL_SCORER", "qwen2.5:7b-instruct"))
    llm_timeout_s: int = field(default_factory=lambda: env_int("LLM_TIMEOUT_S", 60))
    live_llm: bool = field(default_factory=lambda: env_bool("LIVE_LLM", True))       # LLM-phrased follow-ups when a provider is set
    live_llm_timeout_s: float = field(default_factory=lambda: env_float("LIVE_LLM_TIMEOUT_S", 1.4))
    scorer_runs: int = field(default_factory=lambda: env_int("SCORER_RUNS", 3))

    # ---- Speech ----
    # STT: whisper-onnx (built in, local) | faster-whisper (local, optional package) | openai | groq | sarvam | browser | stub
    stt_engine: str = field(default_factory=lambda: env("STT_ENGINE", ""))
    whisper_model: str = field(default_factory=lambda: env("WHISPER_MODEL", "base.en"))
    whisper_threads: int = field(default_factory=lambda: env_int("WHISPER_THREADS", 1))
    stt_language: str = field(default_factory=lambda: env("STT_LANGUAGE", "en"))         # en | hi | auto
    openai_stt_model: str = field(default_factory=lambda: env("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe"))
    groq_stt_model: str = field(default_factory=lambda: env("GROQ_STT_MODEL", "whisper-large-v3-turbo"))
    sarvam_api_key: str = field(default_factory=lambda: env("SARVAM_API_KEY"))
    sarvam_stt_model: str = field(default_factory=lambda: env("SARVAM_STT_MODEL", "saaras:v4"))
    sarvam_tts_model: str = field(default_factory=lambda: env("SARVAM_TTS_MODEL", "bulbul:v2"))
    sarvam_tts_voice: str = field(default_factory=lambda: env("SARVAM_TTS_VOICE", "anushka"))
    # TTS: browser | piper | openai | sarvam | flite (offline, always available with ffmpeg) | stub
    tts_engine: str = field(default_factory=lambda: env("TTS_ENGINE", "browser"))
    tts_replay_engine: str = field(default_factory=lambda: env("TTS_REPLAY_ENGINE", ""))   # voice used in replays when TTS is browser
    openai_tts_model: str = field(default_factory=lambda: env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"))
    openai_tts_voice: str = field(default_factory=lambda: env("OPENAI_TTS_VOICE", "sage"))
    piper_voice: str = field(default_factory=lambda: env("PIPER_VOICE", "en_GB-jenny_dioco-medium"))
    flite_voice: str = field(default_factory=lambda: env("FLITE_VOICE", "slt"))
    stub_stt_latency_ms: int = field(default_factory=lambda: env_int("STUB_STT_LATENCY_MS", 250))
    stub_llm_latency_ms: int = field(default_factory=lambda: env_int("STUB_LLM_LATENCY_MS", 0))

    # ---- Turn taking ----
    endpoint_silence_ms: int = field(default_factory=lambda: env_int("ENDPOINT_SILENCE_MS", 900))
    endpoint_incomplete_ms: int = field(default_factory=lambda: env_int("ENDPOINT_INCOMPLETE_MS", 2200))
    vad_threshold: float = field(default_factory=lambda: env_float("VAD_THRESHOLD", 0.5))

    # ---- Storage and privacy ----
    encrypt_audio: bool = field(default_factory=lambda: env_bool("ENCRYPT_AUDIO", True))
    keep_raw_pcm: bool = field(default_factory=lambda: env_bool("KEEP_RAW_PCM", False))
    retention_days: int = field(default_factory=lambda: env_int("RETENTION_DAYS", 180))

    # ---- Workers and limits ----
    embedded_worker: bool = field(default_factory=lambda: env_bool("EMBEDDED_WORKER", True))
    worker_concurrency: int = field(default_factory=lambda: env_int("WORKER_CONCURRENCY", 2))
    stt_concurrency: int = field(default_factory=lambda: env_int("STT_CONCURRENCY", 0))   # 0 = number of CPU cores
    rate_limit_scale: float = field(default_factory=lambda: env_float("RATE_LIMIT_SCALE", 1.0))
    trust_proxy: bool = field(default_factory=lambda: env_bool("TRUST_PROXY", False))
    max_upload_mb: int = field(default_factory=lambda: env_int("MAX_UPLOAD_MB", 10))
    disconnect_grace_s: int = field(default_factory=lambda: env_int("DISCONNECT_GRACE_S", 120))
    allow_test_hooks: bool = field(default_factory=lambda: env_bool("ALLOW_TEST_HOOKS", False))   # stub STT hints, load tests

    # ---- Accounts ----
    admin_email: str = field(default_factory=lambda: env("ADMIN_EMAIL", "admin@vaani.local"))
    admin_password: str = field(default_factory=lambda: env("ADMIN_PASSWORD", "admin12345"))
    seed_demo: bool = field(default_factory=lambda: env_bool("SEED_DEMO", True))
    consent_contact: str = field(default_factory=lambda: env("CONSENT_CONTACT", ""))   # grievance contact shown on consent
    seed_samples: bool = field(default_factory=lambda: env_bool("SEED_SAMPLES", True))   # import samples/vivas on first start
    session_hours: int = field(default_factory=lambda: env_int("SESSION_HOURS", 12))

    secret_key: str = ""
    encryption_key: str = ""

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'vaani.db'}"
        self.secret_key = _persisted_secret(self.data_dir, "SECRET_KEY", lambda: secrets.token_urlsafe(48))
        self.encryption_key = _persisted_secret(self.data_dir, "DATA_ENCRYPTION_KEY", _fernet_key)
        if not self.llm_provider:
            self.llm_provider = "openai" if self.openai_api_key else ("groq" if self.groq_api_key else "mock")
        if not self.stt_engine:
            self.stt_engine = default_stt_engine(self)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def sessions_dir(self) -> Path:
        path = self.data_dir / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def tts_dir(self) -> Path:
        path = self.data_dir / "tts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def files_dir(self) -> Path:
        path = self.data_dir / "files"
        path.mkdir(parents=True, exist_ok=True)
        return path


def default_stt_engine(s: "Settings") -> str:
    """Best available recogniser: the built-in local Whisper if its model is downloaded, else a hosted API with a
    key, else the browser's own speech recognition."""
    from .voice.stt.whisper_onnx import find_model_dir

    if find_model_dir(s.models_dir, s.whisper_model):
        return "whisper-onnx"
    if s.groq_api_key:
        return "groq"
    if s.openai_api_key:
        return "openai"
    if s.sarvam_api_key:
        return "sarvam"
    return "browser"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(**overrides) -> Settings:
    """Tests: rebuild settings, optionally overriding fields."""
    global _settings
    _settings = Settings()
    for key, value in overrides.items():
        setattr(_settings, key, value)
    if "data_dir" in overrides:
        _settings.data_dir = Path(overrides["data_dir"]).resolve()
        _settings.data_dir.mkdir(parents=True, exist_ok=True)
        if "database_url" not in overrides:
            _settings.database_url = f"sqlite:///{_settings.data_dir / 'vaani.db'}"
    return _settings
