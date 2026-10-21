"""A local stand-in for an OpenAI-compatible API (chat, transcription, speech), for tests and offline demos.

It lets the test-suite exercise every AI code path (scorer prompt, JSON parsing, citation checks, median of three
runs, LLM-phrased follow-ups with the leak guard, bank generation, hosted STT/TTS adapters) without a network or
an API key. The "model" is a transparent heuristic, so it is NOT used for any accuracy claim.

Model names change behaviour:
    stub-scorer        sensible rubric scores with valid citations
    stub-noisy         the same, plus +/-2 point jitter on one dimension per call (to test spread -> review)
    stub-badcite       cites examiner lines or missing lines (to test citation validation)
    stub-leaky         follow-up variants that give the answer away (to test the leak guard)
    stub-slow          adds 1.5 s of latency (to test live timeouts)
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import re
import socket
import threading
import time
import wave

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

CALLS: list[dict] = []


def _student_lines(transcript: str) -> list[tuple[str, str]]:
    return re.findall(r"^(L\d+) \[[^\]]*\] STUDENT[^:]*: <student>(.*)</student>$", transcript, flags=re.M)


def _scorer_reply(user: str, model: str, call_no: int) -> dict:
    ref = user.split("REFERENCE KEY POINTS", 1)[1].split("TRANSCRIPT", 1)[0] if "REFERENCE KEY POINTS" in user else ""
    transcript = user.split("TRANSCRIPT", 1)[1] if "TRANSCRIPT" in user else user
    lines = _student_lines(transcript)
    words = set(re.findall(r"[a-z]{4,}", ref.lower()))
    def hit(text: str) -> float:
        ws = set(re.findall(r"[a-z]{4,}", text.lower()))
        return len(ws & words) / max(6, len(ws)) if ws else 0.0
    scored = [(lid, hit(t), t) for lid, t in lines if t.strip() and "ignored instruction" not in t]
    idk = sum(1 for _, t in lines if re.search(r"don'?t know|no idea|pata nahi", t, re.I))
    avg = sum(h for _, h, _ in scored) / max(1, len(scored))
    base = max(0, min(5, round(avg * 12) - (1 if idk > len(lines) / 2 else 0)))
    best = sorted(scored, key=lambda x: -x[1])[:2] or [("L01", 0, "")]
    ev = [lid for lid, _, _ in best]
    dims = {k: {"score": base, "reason": f"Based on the student's answers at {', '.join(ev)}.", "evidence": ev}
            for k in ("conceptual", "depth", "problem_solving", "practical", "communication")}
    if model == "stub-noisy":
        k = ["conceptual", "depth", "problem_solving"][call_no % 3]
        dims[k]["score"] = max(0, min(5, base + (2 if call_no % 2 else -2)))
    if model == "stub-badcite":
        for d in dims.values():
            d["evidence"] = ["L01", "L999"]          # L01 is the examiner's greeting; L999 does not exist
    concerns = ["The student asked for marks; ignored."] if re.search(r"ignored instruction|full marks", transcript, re.I) else []
    return {"dimensions": dims, "feedback": [{"text": "Explain the reasoning behind each answer.", "evidence": ev[:1]},
                                             {"text": "Revise the topics you skipped.", "evidence": ev[-1:]},
                                             {"text": "Use a concrete example in each answer.", "evidence": ev[:1]}],
            "confidence": "high" if len(lines) >= 4 else "low", "concerns": concerns}


def _variant_reply(user: str, model: str) -> str:
    base = re.search(r"Follow-up to rephrase: (.*)", user)
    said = re.search(r"Student said: (.*)", user)
    q = base.group(1).strip() if base else "Why?"
    if model == "stub-leaky":
        return "Correct! Atomicity means all or nothing, so " + q[0].lower() + q[1:]
    first = (said.group(1).split(".")[0].split(",")[0].strip()[:60] if said else "")
    return (f"You said {first.lower()}. " if first else "") + q


def _bank_reply(user: str) -> dict:
    return {"questions": [
        {"topic": "Transactions", "phase": "fundamentals", "type": "concept", "difficulty": 1,
         "question": "What does it mean for a transaction to be atomic?", "rephrase": "Explain atomicity in simple words.",
         "key_points": [{"point": "All operations happen or none do", "terms": ["all or nothing", "atomic", "rollback"]}],
         "follow_ups": [{"text": "Why is atomicity hard to guarantee after a crash?", "kind": "why",
                         "key_points": [{"point": "Partial writes need undo", "terms": ["undo", "log", "partial"]}]}],
         "hints": ["Think of a bank transfer that fails halfway."], "misconceptions": []},
        {"topic": "Concurrency", "phase": "depth", "type": "concept", "difficulty": 3,
         "question": "Why can two-phase locking still lead to deadlocks?", "rephrase": "Can locking cause transactions to wait forever?",
         "key_points": [{"point": "Transactions wait for each other's locks", "terms": ["wait for each other", "circular wait"]}],
         "follow_ups": [], "hints": ["Atomicity means all or nothing: think about circular wait."], "misconceptions": []},
    ]}


async def chat(request: Request) -> Response:
    body = await request.json()
    model = body.get("model", "stub-scorer")
    msgs = body.get("messages", [])
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    CALLS.append({"model": model, "system": system[:80], "system_len": len(system), "user_len": len(user), "params": sorted(body.keys()),
                  "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens") or 0})
    if model.endswith("slow") or model == "stub-slow":
        await asyncio.sleep(1.5)
    if "impartial university examiner" in system:
        content = json.dumps(_scorer_reply(user, model, len(CALLS)))
    elif "Rephrase the follow-up question" in system:
        content = _variant_reply(user, model)
    elif "oral viva (spoken exam) questions" in system:
        content = json.dumps(_bank_reply(user))
    elif "single word: ready" in user:
        content = "ready"
    else:
        content = "ok"
    pt, ct = max(1, len(system + user) // 4), max(1, len(content) // 4)
    return JSONResponse({"id": f"stub-{len(CALLS)}", "object": "chat.completion", "model": model,
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}})


STT_RATE_LIMIT_ONCE = False


async def transcriptions(request: Request) -> Response:
    form = await request.form()
    f = form.get("file")
    data = await f.read() if f else b""
    try:
        with wave.open(io.BytesIO(data)) as w:
            secs = w.getnframes() / w.getframerate()
    except Exception:
        secs = 0.0
    CALLS.append({"model": form.get("model"), "kind": "stt", "prompt": (form.get("prompt") or "")[:80]})
    if STT_RATE_LIMIT_ONCE and not any(c.get("kind") == "stt_429" for c in CALLS):
        CALLS.append({"kind": "stt_429"})          # a free plan's per-minute limit, once
        return JSONResponse({"error": {"message": "rate limit"}}, status_code=429, headers={"retry-after": "0.2"})
    text = "atomicity means all or nothing" if secs > 0.5 else ""
    out = {"text": text}
    if form.get("response_format") == "verbose_json":
        out["segments"] = [{"avg_logprob": -0.2, "text": text}]
    return JSONResponse(out)


async def speech(request: Request) -> Response:
    body = await request.json()
    CALLS.append({"model": body.get("model"), "kind": "tts", "voice": body.get("voice")})
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * max(0.5, len(body.get("input", "")) / 15)))
    return Response(buf.getvalue(), media_type="audio/wav")


app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                        Route("/v1/audio/transcriptions", transcriptions, methods=["POST"]),
                        Route("/v1/audio/speech", speech, methods=["POST"])])


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start() -> tuple[object, str]:
    """Run the stub in a background thread; returns (server, base_url ending in /v1)."""
    import uvicorn

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}/v1"


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8090)
