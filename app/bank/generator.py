"""Draft questions from a syllabus. Drafts are never used in a viva until a faculty member approves them.

With an AI provider, the model writes questions in the bank format (key points, follow-ups, a hint that must not give
the answer away, misconceptions). Offline, a simple template generator turns each syllabus line into a starter
question that faculty then edit; it is honest about being basic.
"""
from __future__ import annotations

import hashlib
import json
import re

from pydantic import ValidationError

from ..llm.providers import chat, resolve
from .models import Question, hint_leaks

GEN_PROMPT_VERSION = "bankgen-1.0"
SYSTEM = """You write oral viva (spoken exam) questions for university faculty to review. Output JSON only.
Each question must be answerable aloud in about a minute. For each question give:
- topic (short), phase (fundamentals | depth | applied), type (concept | scenario | debug), difficulty (1 easy, 2 medium, 3 hard)
- question (one or two sentences, spoken style), rephrase (same question in simpler words)
- key_points: 2-4 items, each {"point": "...", "terms": ["words or short phrases a student might say for this point", ...]} with 3-6 terms
- follow_ups: 1-2 items {"text": "a 'why' or 'what if' question", "kind": "why" | "what_if" | "deeper", "key_points": [...]}
- hints: exactly 1 hint that nudges thinking with an analogy or a scenario WITHOUT naming any key point term
- misconceptions: 0-2 items {"text": "...", "terms": [...]}
Applied questions describe a short realistic scenario or a small piece of buggy code (put code in "code_snippet").
Return {"questions": [...]}."""


async def generate_from_syllabus(subject: str, syllabus: str, counts: dict[str, int], aliases: dict | None = None) -> dict:
    syllabus = syllabus.strip()[:12000]
    meta = {"prompt_version": GEN_PROMPT_VERSION, "syllabus_sha": hashlib.sha256(syllabus.encode()).hexdigest()[:12]}
    cfg = resolve()
    if cfg.is_mock:
        qs = template_questions(subject, syllabus, counts)
        return {"questions": qs, "errors": [], "meta": {**meta, "model": "offline-template"}, "offline": True}
    want = ", ".join(f"{n} {p}" for p, n in counts.items() if n)
    user = f"Subject: {subject}\nWrite {want} questions covering this syllabus:\n{syllabus}"
    res = await chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], feature="bank_generation",
                     role="scorer", json_mode=True, temperature=0.4, max_tokens=6000, retries=2, seed=None,
                     mock=lambda: {"questions": template_questions(subject, syllabus, counts)})
    raw = (res.data or {}).get("questions") if isinstance(res.data, dict) else None
    questions, errors = [], []
    for i, item in enumerate(raw or [], start=1):
        try:
            q = Question.model_validate({**item, "code": ""}).model_dump()
        except ValidationError as exc:
            errors.append(f"draft {i}: {exc.errors()[0]['msg']}")
            continue
        leaks = [l for h in q["hints"] for l in hint_leaks(h, q["question"], q["key_points"], aliases)]
        if leaks:
            q["hints"] = []          # a hint that gives the answer away is dropped; faculty can write a new one
            errors.append(f"draft {i}: hint removed because it named {', '.join(sorted(set(leaks))[:2])}")
        questions.append(q)
    return {"questions": questions, "errors": errors, "meta": {**meta, "model": res.model}, "offline": res.provider == "mock"}


def template_questions(subject: str, syllabus: str, counts: dict[str, int]) -> list[dict]:
    """Offline drafts: one starter question per syllabus line ("Topic: sub-topic, sub-topic")."""
    lines = []
    for raw in re.split(r"[\n;]+", syllabus):
        s = re.sub(r"^\s*(unit|module|chapter)?\s*[\dIVX]+[.):-]?\s*", "", raw.strip(), flags=re.I).strip(" -•*\t")
        if len(s) >= 4:
            lines.append(s)
    out = []
    phases = [p for p, n in counts.items() for _ in range(n)]
    for i, phase in enumerate(phases):
        if not lines:
            break
        line = lines[i % len(lines)]
        head, _, tail = line.partition(":")
        subs = [t.strip() for t in re.split(r",|/| and ", tail) if len(t.strip()) > 2][:4] if tail else []
        topic = head.strip()[:60] or subject
        if phase == "applied":
            text = f"Describe a real situation where {topic.lower()} matters, and walk me through how you would handle it."
            qtype = "scenario"
        elif phase == "depth":
            text = f"Why is {topic.lower()} designed the way it is? What would go wrong without it?"
            qtype = "concept"
        else:
            text = f"Explain {topic.lower()}" + (f", including {', '.join(subs[:3])}." if subs else ".")
            qtype = "concept"
        kps = [{"point": s[0].upper() + s[1:], "terms": [s]} for s in subs] or [{"point": topic, "terms": [topic]}]
        out.append(Question.model_validate({
            "topic": topic, "phase": phase, "type": qtype, "difficulty": 2 if phase != "depth" else 3, "question": text,
            "rephrase": f"What do you know about {topic.lower()}?", "key_points": kps,
            "follow_ups": [{"text": f"Can you give a concrete example of {topic.lower()}?", "kind": "example"}],
            "hints": [], "tags": ["draft", "offline-template"],
        }).model_dump())
    return out


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
