"""Passwords, session tokens, CSRF, rate limiting and data encryption."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import timedelta

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings
from .db import utcnow

SESSION_COOKIE = "vaani_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "vaani"


# ----------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# ----------------------------------------------------------------------------- sessions
def create_session_token(user_id: int, role: str, extra: dict | None = None) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.session_hours)).timestamp()),
        "jti": secrets.token_hex(8),
        **(extra or {}),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def signed_token(data: str, ttl_s: int = 3600) -> str:
    """Short-lived signed token for links such as file downloads in <audio> tags."""
    exp = int(time.time()) + ttl_s
    msg = f"{data}|{exp}"
    sig = hmac.new(get_settings().secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode().rstrip("=")


def verify_signed_token(token: str) -> str | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        data, exp, sig = base64.urlsafe_b64decode(padded.encode()).decode().rsplit("|", 2)
        msg = f"{data}|{exp}"
        good = hmac.new(get_settings().secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(good, sig) or int(exp) < time.time():
            return None
        return data
    except (ValueError, TypeError):
        return None


# ----------------------------------------------------------------------------- encryption at rest
_fernet: Fernet | None = None


def fernet() -> Fernet:
    global _fernet
    key = get_settings().encryption_key
    if _fernet is None or getattr(_fernet, "_vaani_key", None) != key:
        _fernet = Fernet(key.encode())
        _fernet._vaani_key = key  # type: ignore[attr-defined]
    return _fernet


def encrypt_bytes(data: bytes) -> bytes:
    return fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return fernet().decrypt(token)
    except InvalidToken as exc:  # pragma: no cover - only with a rotated/wrong key
        raise ValueError("Could not decrypt stored file (wrong DATA_ENCRYPTION_KEY?)") from exc


def encrypt_text(text: str) -> str:
    return encrypt_bytes(text.encode()).decode()


def decrypt_text(token: str) -> str:
    return decrypt_bytes(token.encode()).decode()


# ----------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window limiter kept in memory (per process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # keep memory bounded
                for k in list(self._hits)[:10_000]:
                    self._hits.pop(k, None)
            return True

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits.setdefault(key, deque()).append(time.monotonic())

    def count(self, key: str, window_s: float) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            while q and now - q[0] > window_s:
                q.popleft()
            return len(q)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
failed_logins = RateLimiter()   # failed password attempts per account (brute-force protection)
