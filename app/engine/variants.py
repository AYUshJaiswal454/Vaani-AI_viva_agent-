"""Optional LLM-phrased follow-ups ("logged variants").

When a language model is configured, a bank follow-up can be re-worded to connect to what the student just said
("You mentioned a log. Why must it be written before the data page?"). The variant is accepted only if it passes the
leak guard: it must not evaluate the answer ("correct", "exactly"), must not contain any key-point term the student
has not already said, must stay short, and must still be a question. Otherwise the bank wording is used. Every
variant is stored in the transcript next to the bank follow-up it varies, so it stays traceable.
"""
from __future__ import annotations

import asyncio
import re

from ..config import get_settings
from ..llm.providers import chat, resolve
from .text import EVALUATIVE, normalize, phrase_in, term_tokens, tokens

VARIANT_PROMPT = """You are a university viva examiner speaking aloud. Rephrase the follow-up question so it naturally refers to something the student just said. Rules: at most 30 words; keep the same meaning and difficulty; do NOT evaluate the answer (never say correct, wrong, good, exactly); do NOT give away any part of the answer or add new technical terms; end with a question mark. Reply with the question only."""


def leak_check(variant: str, base: str, student_answer: str, key_points: list[str], aliases: dict | None = None) -> str:
    """Return '' if the variant is safe, otherwise the reason it was rejected."""
    v = variant.strip()
    if not v or len(v.split()) > 40:
        return "too long or empty"
    if not v.endswith("?"):
        return "not a question"
    if EVALUATIVE.search(v):
        return "evaluates the answer"
    vt = tokens(normalize(v, aliases))
    allowed = set(tokens(normalize(base + " " + student_answer, aliases)))
    for kp in key_points:
        tt = term_tokens(kp.split(":")[0], aliases)
        if len(" ".join(tt)) >= 4 and phrase_in(tt, vt, max_gap=1) and not all(t in allowed for t in tt):
            return f"mentions '{kp.split(':')[0]}'"
    return ""


async def phrase_followup(req: dict, session_id: int | None, aliases: dict | None = None) -> tuple[str | None, dict]:
    """Return (variant text or None, log info). Never waits longer than LIVE_LLM_TIMEOUT_S."""
    s = get_settings()
    cfg = resolve()
    if cfg.is_mock or not s.live_llm:
        return None, {"used": False, "why": "no live model"}
    msgs = [{"role": "system", "content": VARIANT_PROMPT},
            {"role": "user", "content": f"Main question: {req['question']}\nStudent said: {req['answer'][:800]}\n"
                                        f"Follow-up to rephrase: {req['base']}"}]
    try:
        res = await asyncio.wait_for(chat(msgs, feature="examiner", role="live", session_id=session_id, temperature=0.3,
                                          max_tokens=80, retries=0, timeout_s=s.live_llm_timeout_s, seed=None),
                                     timeout=s.live_llm_timeout_s + 0.2)
    except Exception as exc:  # timeout or provider error: the bank wording is used
        return None, {"used": False, "why": f"{type(exc).__name__}"}
    if res.provider == "mock":
        return None, {"used": False, "why": "provider unavailable"}
    text = re.sub(r"\s+", " ", res.text or "").strip().strip('"')
    why = leak_check(text, req["base"], req["answer"], req.get("key_points", []), aliases)
    if why:
        return None, {"used": False, "why": f"rejected by leak guard: {why}", "candidate": text[:300], "ms": res.latency_ms}
    return text, {"used": True, "ms": res.latency_ms, "model": res.model}
