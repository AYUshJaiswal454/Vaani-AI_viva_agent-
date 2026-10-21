"""LLM access for the examiner (live, fast model), the scorer (after the viva, stronger model) and bank generation.

* One OpenAI-compatible client serves OpenAI, Groq and Ollama (all expose /v1/chat/completions), and any other
  compatible endpoint (vLLM, Azure, Gemini) through a custom base URL.
* "mock" is the deterministic offline mode: every feature supplies its own rule-based fallback, so the whole
  product works with no API key and no GPU (also used by the load tests).
* Requests adapt to the model: parameters a model rejects (temperature, seed, reasoning_effort, response_format)
  are dropped and the call is retried.
* 429/5xx are retried with exponential backoff + jitter (honouring Retry-After); repeated failures open a short
  circuit breaker so the live examiner falls back to the question bank instantly instead of waiting.
* Deterministic calls (temperature 0) can be cached by content hash.
* Every call is written to the usage ledger with tokens and cost (the cost sheet is computed from it).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ..config import DATA_FILES, get_settings
from ..db import get_db, now_iso
from ..net.http import client

log = logging.getLogger("vaani.llm")
PRICING = json.loads((DATA_FILES / "pricing.json").read_text(encoding="utf-8"))


class LLMUnavailable(Exception):
    """Raised when no model can answer; callers fall back to deterministic output."""


@dataclass
class ProviderConfig:
    name: str                 # openai | groq | ollama | mock
    base_url: str = ""
    api_key: str = ""
    model_live: str = ""
    model_scorer: str = ""
    reasoning_live: str = ""
    reasoning_scorer: str = ""

    @property
    def is_mock(self) -> bool:
        return self.name == "mock"

    def model(self, role: str) -> str:
        return self.model_scorer if role == "scorer" else self.model_live

    def reasoning(self, role: str) -> str:
        return self.reasoning_scorer if role == "scorer" else self.reasoning_live


@dataclass
class LLMResult:
    text: str
    data: Any = None
    model: str = ""
    provider: str = ""
    cached: bool = False
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0


def resolve(provider: str | None = None) -> ProviderConfig:
    s = get_settings()
    name = provider or s.llm_provider
    if name == "openai" and s.openai_api_key:
        return ProviderConfig("openai", s.openai_base_url, s.openai_api_key, s.openai_model_live, s.openai_model_scorer,
                              s.openai_reasoning_live, s.openai_reasoning_scorer)
    if name == "groq" and s.groq_api_key:
        return ProviderConfig("groq", s.groq_base_url, s.groq_api_key, s.groq_model_live, s.groq_model_scorer, "low", "low")
    if name == "ollama":
        return ProviderConfig("ollama", s.ollama_base_url, "ollama", s.ollama_model_live, s.ollama_model_scorer)
    return ProviderConfig("mock")


def provider_status() -> dict:
    cfg = resolve()
    return {"provider": cfg.name, "live_model": cfg.model_live or "deterministic",
            "scorer_model": cfg.model_scorer or "deterministic rubric", "breaker_open": _breaker.is_open(cfg.name)}


# ----------------------------------------------------------------------------- cost accounting
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached: int = 0) -> float:
    price = PRICING["models"].get(model)
    if not price:
        return 0.0
    fresh = max(0, input_tokens - cached)
    return (fresh * price["input"] + cached * price.get("cached_input", price["input"]) + output_tokens * price["output"]) / 1e6


def record_usage(session_id: int | None, feature: str, provider: str, model: str, usage: dict, latency_ms: int,
                 estimated: bool = False, audio_seconds: float = 0.0, characters: int = 0, cost: float | None = None) -> None:
    inp, out, cached = int(usage.get("input", 0)), int(usage.get("output", 0)), int(usage.get("cached", 0))
    if cost is None:
        cost = 0.0 if provider in ("ollama", "mock", "local") else cost_usd(model, inp, out, cached)
    try:
        get_db().insert("usage_ledger", {
            "session_id": session_id, "feature": feature, "provider": provider, "model": model, "input_tokens": inp,
            "output_tokens": out, "cached_tokens": cached, "audio_seconds": round(audio_seconds, 2), "characters": characters,
            "cost_usd": round(cost, 7), "latency_ms": latency_ms, "estimated": int(estimated), "created_at": now_iso(),
        })
    except Exception:  # pragma: no cover - accounting must never break a feature
        log.exception("usage ledger write failed")


# ----------------------------------------------------------------------------- circuit breaker
class Breaker:
    def __init__(self, threshold: int = 5, cooldown_s: float = 45.0) -> None:
        self.failures: dict[str, int] = {}
        self.opened: dict[str, float] = {}
        self.threshold = threshold
        self.cooldown = cooldown_s

    def is_open(self, key: str) -> bool:
        t = self.opened.get(key)
        if t and time.monotonic() - t < self.cooldown:
            return True
        if t:
            self.opened.pop(key, None)
            self.failures[key] = self.threshold - 1   # half-open: one more failure re-opens
        return False

    def success(self, key: str) -> None:
        self.failures[key] = 0

    def failure(self, key: str) -> None:
        self.failures[key] = self.failures.get(key, 0) + 1
        if self.failures[key] >= self.threshold:
            self.opened[key] = time.monotonic()


_breaker = Breaker()
_concurrency: dict[str, asyncio.Semaphore] = {}


def _sem(cfg: ProviderConfig) -> asyncio.Semaphore:
    limit = 4 if cfg.name == "ollama" else 64     # a local GPU serves few requests at once
    key = f"{cfg.name}:{cfg.base_url}:{id(asyncio.get_running_loop())}"   # asyncio primitives belong to one loop
    if len(_concurrency) > 64:
        _concurrency.clear()
    if key not in _concurrency:
        _concurrency[key] = asyncio.Semaphore(limit)
    return _concurrency[key]


# ----------------------------------------------------------------------------- core call
UNSUPPORTED = re.compile(r"(unsupported|not supported|unrecognized|unknown|does not support|invalid).{0,80}?"
                         r"(temperature|seed|reasoning_effort|response_format|max_tokens|max_completion_tokens|tools|stream_options)", re.I | re.S)


def _cache_key(cfg: ProviderConfig, model: str, payload: dict) -> str:
    raw = json.dumps({"p": cfg.name, "m": model, "b": cfg.base_url, "x": payload}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


_UNSUPPORTED_BY_MODEL: dict[tuple[str, str], set[str]] = {}


def _adapt(payload: dict, param: str) -> None:
    """Remove (or rename) a parameter the model rejected, e.g. temperature on reasoning models."""
    if param == "max_tokens" and "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    elif param == "max_completion_tokens" and "max_completion_tokens" in payload:
        payload["max_tokens"] = payload.pop("max_completion_tokens")
    else:
        payload.pop(param, None)
        if param == "temperature":
            payload.pop("seed", None)


async def chat(messages: list[dict], *, feature: str, role: str = "live", session_id: int | None = None,
               json_mode: bool = False, temperature: float | None = 0.0, max_tokens: int = 600,
               mock: Callable[[], Any] | None = None, cache: bool | None = None, timeout_s: float | None = None,
               retries: int = 4, provider: str | None = None, seed: int | None = 7) -> LLMResult:
    """One chat completion. `mock` returns the deterministic answer used in mock mode or as the fallback.

    role="live": the examiner's fast model, short timeout, no retries (the caller falls back to the bank at once).
    role="scorer": the stronger model, run after the viva, with retries.
    """
    cfg = resolve(provider)
    t0 = time.perf_counter()
    if cfg.is_mock or _breaker.is_open(cfg.name):
        if mock is None:
            raise LLMUnavailable("No AI provider configured")
        stub_ms = get_settings().stub_llm_latency_ms
        if stub_ms:
            await asyncio.sleep(stub_ms / 1000 * (0.8 + 0.4 * random.random()))
        out = mock()
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        ms = int((time.perf_counter() - t0) * 1000)
        record_usage(session_id, feature, "mock", "deterministic", {"input": prompt_chars // 4, "output": estimate_tokens(text)},
                     ms, estimated=True)
        return LLMResult(text=text, data=out if not isinstance(out, str) else None, model="deterministic", provider="mock",
                         latency_ms=ms)

    model = cfg.model(role)
    payload: dict[str, Any] = {"model": model, "messages": messages,
                               "max_completion_tokens" if cfg.name == "openai" else "max_tokens": max_tokens}
    if temperature is not None:
        payload["temperature"] = temperature
        if temperature == 0 and seed is not None:
            payload["seed"] = seed
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    effort = cfg.reasoning(role)
    if cfg.name in ("openai", "groq") and effort:
        payload["reasoning_effort"] = effort
    for param in _UNSUPPORTED_BY_MODEL.get((cfg.base_url, model), ()):   # learned from earlier 400s: don't send again
        _adapt(payload, param)
    use_cache = bool(cache)
    key = _cache_key(cfg, model, {k: v for k, v in payload.items() if k != "model"}) if use_cache else None
    if key:
        row = get_db().fetchone("SELECT response FROM llm_cache WHERE key = ?", (key,))
        if row:
            data = json.loads(row["response"])
            return LLMResult(text=data["text"], data=_maybe_json(data["text"]) if json_mode else None, model=model,
                             provider=cfg.name, cached=True)

    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {"authorization": f"Bearer {cfg.api_key}", "content-type": "application/json"}
    timeout = timeout_s or get_settings().llm_timeout_s
    last_error = ""
    async with _sem(cfg):
        for attempt in range(max(1, retries + 1)):
            t0 = time.perf_counter()
            try:
                resp = await client().post(url, json=payload, headers=headers, timeout=timeout)
            except Exception as exc:  # network error / timeout
                last_error = f"{type(exc).__name__}: {exc}"
                _breaker.failure(cfg.name)
                if attempt < retries:
                    await asyncio.sleep(min(8, 0.5 * 2 ** attempt) + random.random() * 0.3)
                continue
            if resp.status_code == 400:
                body = resp.text[:600]
                m = UNSUPPORTED.search(body)
                if m:
                    param = m.group(2).lower()
                    _UNSUPPORTED_BY_MODEL.setdefault((cfg.base_url, model), set()).add(param)
                    _adapt(payload, param)
                    continue
                last_error = f"400: {body}"
                break
            if resp.status_code in (429, 500, 502, 503, 504):
                if resp.status_code != 429:       # a rate limit is not an outage: it must not open the circuit breaker
                    _breaker.failure(cfg.name)
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 0.6 * 2 ** attempt
                last_error = f"{resp.status_code}: {resp.text[:200]}"
                if attempt < retries:
                    # free plans limit tokens per minute: wait as long as the provider asks (up to a minute)
                    await asyncio.sleep(min(60 if resp.status_code == 429 else 20, delay) + random.random() * 0.4)
                continue
            if resp.status_code >= 400:
                last_error = f"{resp.status_code}: {resp.text[:300]}"
                break
            _breaker.success(cfg.name)
            body = resp.json()
            choice = (body.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content") or ""
            u = body.get("usage") or {}
            usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
                     "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)}
            ms = int((time.perf_counter() - t0) * 1000)
            record_usage(session_id, feature, cfg.name, model, usage, ms)
            if key:
                get_db().execute("INSERT INTO llm_cache (key, response, created_at) VALUES (?, ?, ?) ON CONFLICT(key) DO NOTHING",
                                 (key, json.dumps({"text": text}), now_iso()))
            return LLMResult(text=text, data=_maybe_json(text) if json_mode else None, model=model, provider=cfg.name,
                             usage=usage, latency_ms=ms)
    log.warning("LLM call failed (%s/%s): %s", cfg.name, model, last_error)
    if mock is not None:
        out = mock()
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        return LLMResult(text=text, data=out if not isinstance(out, str) else None, model="deterministic-fallback",
                         provider="mock", latency_ms=int((time.perf_counter() - t0) * 1000))
    raise LLMUnavailable(last_error or "AI provider unavailable")


def _maybe_json(text: str) -> Any:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{[\s\S]*\}", t)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


async def test_connection(provider: str | None = None) -> dict:
    cfg = resolve(provider)
    if cfg.is_mock:
        return {"ok": True, "provider": "mock", "message": "Offline deterministic mode (no AI provider)."}
    try:
        res = await chat([{"role": "user", "content": "Reply with the single word: ready"}], feature="settings_test",
                         max_tokens=20, timeout_s=30, retries=1)
        return {"ok": res.provider != "mock", "provider": cfg.name, "model": res.model, "message": res.text.strip()[:60]}
    except LLMUnavailable as exc:
        return {"ok": False, "provider": cfg.name, "model": cfg.model_live, "message": str(exc)[:300]}
