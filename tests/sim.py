"""Run a complete viva between the examiner engine and a scripted persona on a simulated clock.

Fast (a full 15-minute viva takes a few milliseconds) and deterministic, so the test-suite can check timing rules,
follow-up policy and scoring on many vivas. The same harness feeds the demo data and the consistency evaluation.
"""
from __future__ import annotations

import random

from app.bank.models import bank_vocabulary
from app.engine.examiner import Examiner, make_plan

CHARS_PER_S = 15.0
ENDPOINT_S = 0.9


def speak_seconds(action: dict) -> float:
    return max(0.8, sum(len(s["text"]) for s in action.get("segments", [])) / CHARS_PER_S)


def run_viva(questions: list[dict], persona, *, subject: str = "Database Management Systems", aliases: dict | None = None,
             student_name: str = "Asha Verma", roll_no: str = "22CS1047", seed: str = "viva-1:22CS1047",
             difficulty: str = "mixed", phases: list[dict] | None = None, max_t: float = 960.0) -> dict:
    qmap = {q["code"]: q for q in questions}
    plan = make_plan(questions, seed, difficulty)
    ctx = {"student_name": student_name, "roll_no": roll_no, "subject": subject, "plan": plan,
           "vocab": bank_vocabulary(questions, aliases), "aliases": aliases or {}, "phases": phases}
    ex = Examiner(None, qmap, ctx)
    rng = random.Random(hash(seed) & 0xFFFF)
    t = 0.0
    transcript: list[dict] = []
    flags: list[dict] = []
    pending = ex.start(t)
    current_q = None
    ended = None

    def handle(actions):
        nonlocal t, current_q, ended
        follow = []
        for act in actions:
            if act["type"] == "flag":
                flags.append(act)
            elif act["type"] == "end":
                ended = act
            elif act["type"] in ("say", "cut"):
                text = " ".join(s["text"] for s in act["segments"])
                transcript.append({"speaker": "examiner", "t": round(t, 1), "kind": act["kind"], "text": text,
                                   "sources": [s["source"] for s in act["segments"]], "code": act.get("code"),
                                   "reason": act.get("reason"), "phase": act.get("phase"),
                                   "t_end": round(t + speak_seconds(act), 1), "segments": act["segments"]})
                if act.get("log_integrity"):
                    flags.append({"type": "flag", **act["log_integrity"]})
                if act.get("code"):
                    current_q = qmap.get(act["code"])
                elif act["kind"] in ("prompt",) and ex.st["step"] == "identity":
                    current_q = None
                t += speak_seconds(act)
                if act["type"] == "say":
                    follow += ex.playback_done(t)
        return follow

    last_say = None
    guard = 0
    while ended is None and t < max_t and guard < 2000:
        guard += 1
        says = [a for a in pending if a["type"] == "say"]
        if says:
            last_say = says[-1]
        more = handle(pending)
        pending = more
        if ended or pending:
            continue
        if ex.st["mode"] == "ended":
            break
        if ex.st["mode"] != "listening":
            # examiner is closing or waiting: advance the clock with ticks
            t += 0.5
            pending = ex.tick(t)
            continue
        say = last_say or {}
        q_for_answer = current_q if ex.st["step"] not in ("identity", "wrapup_q") else None
        if say.get("kind") == "wrapup":
            q_for_answer = None
        resp = persona.respond(say, q_for_answer) if say else None
        if resp is None:
            # silence: let the timers act
            for _ in range(200):
                t += 0.5
                pending = ex.tick(t)
                if pending:
                    break
            continue
        text, dur = resp
        think = rng.uniform(*persona.think_s)
        # timers may fire while the student is still thinking
        stop = t + think
        while t + 0.5 < stop:
            t += 0.5
            fired = ex.tick(t)
            if fired:
                pending = fired
                break
        if pending:
            continue
        t = stop
        ex.speech_started(t)
        start = t
        cut_act = None
        while t < start + dur:
            t = min(start + dur, t + 1.0)
            fired = ex.tick(t)
            if any(a["type"] == "cut" for a in fired):
                cut_act = fired
                break
            if any(a["type"] == "end" for a in fired):
                pending = fired
                break
        if pending:
            continue
        if cut_act:
            spoken = int(len(text.split()) * (t - start) / dur)
            text = " ".join(text.split()[:max(1, spoken)])
            transcript.append({"speaker": "student", "t": round(start, 1), "t_end": round(t, 1), "text": text, "cut": True})
            handle(cut_act)
            pending = ex.answer(text, t, {"cut": True})
            transcript[transcript.index(next(r for r in reversed(transcript) if r["speaker"] == "student"))]["answer"] = ex.last_answer
            continue
        ex.speech_stopped(t)
        transcript.append({"speaker": "student", "t": round(start, 1), "t_end": round(t, 1), "text": text})
        t += ENDPOINT_S
        pending = ex.answer(text, t)
        transcript[-1]["answer"] = ex.last_answer
    return {"transcript": transcript, "turns": to_turns(transcript), "state": ex.st, "summary": ex.summary(),
            "flags": flags, "plan": plan, "ended": ended, "duration": round(t, 1)}


def to_turns(transcript: list[dict]) -> list[dict]:
    """The simulator's transcript in the same shape as rows of the `turns` table."""
    out = []
    for i, r in enumerate(transcript, start=1):
        out.append({"seq": i, "speaker": r["speaker"], "kind": r.get("kind", "answer"), "phase": r.get("phase"),
                    "text": r["text"], "question_code": r.get("code"), "t_start": r["t"], "t_end": r.get("t_end"),
                    "source": ",".join(r.get("sources", [])) if r.get("sources") else None,
                    "meta": {"answer": r.get("answer")} if r["speaker"] == "student" else {"reason": r.get("reason")}})
    return out
