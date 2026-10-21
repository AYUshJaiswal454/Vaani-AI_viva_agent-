"""The live examiner: a deterministic, time-boxed state machine.

It follows the brief's 15-minute flow on one shared clock:

    0:00-1:00   warm-up       identity, rules, project opener
    1:00-5:00   fundamentals  2-3 bank questions, at most one follow-up each
    5:00-10:00  depth         the student's strongest topic so far: "why" and "what if" chains
    10:00-13:00 applied       a scenario or debugging problem, reasoned out loud (code shown on screen if any)
    13:00-14:30 wrap-up       anything to add, polite close
    15:00       hard stop     scoring runs afterwards on the full transcript, never live

and these rules for every answer:

    strong answer   -> go deeper (a bank follow-up, preferring one that matches what the student said)
    partial / weak  -> one hint, then move on
    "I don't know"  -> move on with no penalty spiral: next question is easier and from another topic
    off-topic       -> redirect once, then move on
    longer than 90 s -> polite interruption
    silence         -> offer to repeat (10 s), rephrase (22 s), move on (35 s)
    "repeat" / "what do you mean" / "let me think" -> repeat / rephrase / wait

Every utterance is traceable: bank questions, follow-ups and hints carry their bank code (DBMS-F-03, DBMS-F-03/f1,
DBMS-F-03/h1); system lines carry `system:<id>`; LLM-phrased variants are logged with the follow-up they vary.
The engine is pure: (state, event, clock) -> (new state, actions). It never waits for a network call, so it is easy
to test with simulated time and safe to run 150 copies at once.
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from . import phrases
from .assess import Assessment, assess
from .text import EVALUATIVE, normalize, phrase_in, term_tokens, tokens

ENGINE_VERSION = "examiner-1.2"
DEFAULT_PHASES = [
    {"id": "warmup", "start": 0, "end": 60},
    {"id": "fundamentals", "start": 60, "end": 300},
    {"id": "depth", "start": 300, "end": 600},
    {"id": "applied", "start": 600, "end": 780},
    {"id": "wrapup", "start": 780, "end": 870},
]
HARD_STOP_S = 900
ANSWER_CAP_S = 90
SILENCE_PROMPT_S, SILENCE_REPHRASE_S, SILENCE_MOVE_ON_S = 10, 22, 35
MAX_FOLLOWUPS = {"warmup": 1, "fundamentals": 1, "depth": 3, "applied": 2}
MIN_LEFT_FOR_FOLLOWUP = {"warmup": 0, "fundamentals": 25, "depth": 30, "applied": 30}
MIN_LEFT_FOR_QUESTION = {"fundamentals": 40, "depth": 60, "applied": 75}
# The minimum per phase before the next phase may start early, and the most we ask when a student answers quickly.
# Quick answerers get more questions rather than a shorter viva: the time is the student's chance to show what they
# know, so a phase hands over early only when another question would not fit (EXTRA_NEEDS_S) or the bank runs out.
QUOTA = {"fundamentals": 3, "depth": 2, "applied": 1}
MAX_MAIN = {"fundamentals": 10, "depth": 8, "applied": 5}
EXTRA_NEEDS_S = {"fundamentals": 45, "depth": 60, "applied": 90}   # time left in the phase to go beyond the quota
FOLLOWUP_PREFERENCE = {
    "warmup": ["probe", "why", "deeper", "example", "what_if"],
    "fundamentals": ["why", "deeper", "example", "what_if", "probe"],
    "depth": ["why", "what_if", "deeper", "example", "probe"],
    "applied": ["why", "what_if", "probe", "deeper", "example"],
}


def validate_phase_plan(plan: list[dict] | None, duration_s: int = HARD_STOP_S) -> list[dict]:
    """A customised plan must keep the five phases in order, inside the time limit, ending 30 s before the hard stop."""
    if not plan:
        return [dict(p) for p in DEFAULT_PHASES]
    ids = [p.get("id") for p in plan]
    if ids != [p["id"] for p in DEFAULT_PHASES]:
        raise ValueError("Phases must be warmup, fundamentals, depth, applied, wrapup in that order")
    prev = 0
    out = []
    for p in plan:
        s, e = int(p["start"]), int(p["end"])
        if s != prev or e <= s:
            raise ValueError(f"Phase {p['id']} must start where the previous one ends and have positive length")
        out.append({"id": p["id"], "start": s, "end": e})
        prev = e
    if prev > duration_s - 30:
        raise ValueError("Wrap-up must finish at least 30 seconds before the hard stop")
    return out


def make_plan(questions: list[dict], seed_text: str, difficulty: str = "mixed", topics: list[str] | None = None) -> dict:
    """Choose this student's questions from the approved bank, deterministically from (viva, student).

    Different students get different but equivalent sets (same phases, spread over topics, same difficulty mix),
    which limits answer sharing between students; the plan is stored, so every question is traceable.
    """
    rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest()[:12], 16))
    pool = [q for q in questions if q.get("status", "approved") == "approved"]
    if topics:
        wanted = {t.lower() for t in topics}
        scoped = [q for q in pool if q["topic"].lower() in wanted or q["phase"] == "warmup"]
        if sum(1 for q in scoped if q["phase"] == "fundamentals") >= 3:
            pool = scoped

    def by_phase(phase: str) -> list[dict]:
        items = [q for q in pool if q["phase"] == phase]
        rng.shuffle(items)
        return items

    openers = by_phase("warmup")
    fund = by_phase("fundamentals")
    # Spread over topics: round-robin across topics in shuffled order.
    by_topic: dict[str, list[dict]] = {}
    for q in fund:
        by_topic.setdefault(q["topic"], []).append(q)
    order = list(by_topic)
    rng.shuffle(order)
    spread = []
    while any(by_topic.values()):
        for t in order:
            if by_topic[t]:
                spread.append(by_topic[t].pop(0))
    depth: dict[str, list[str]] = {}
    for q in by_phase("depth"):
        depth.setdefault(q["topic"], []).append(q["code"])
    start_diff = {"easy": 1, "medium": 2, "hard": 3, "mixed": 1}.get(difficulty, 2)
    return {
        "seed": seed_text,
        "opener": openers[0]["code"] if openers else None,
        "fundamentals": [q["code"] for q in spread],
        "depth": depth,
        "applied": [q["code"] for q in by_phase("applied")],
        "start_difficulty": start_diff,
        "difficulty_mode": difficulty,
    }


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


class Examiner:
    def __init__(self, state: dict | None, questions: dict[str, dict], ctx: dict) -> None:
        """questions: code -> question dict. ctx: student_name, roll_no, subject, vocab, aliases, phases, plan."""
        self.q = questions
        self.ctx = ctx
        self.phases = ctx.get("phases") or DEFAULT_PHASES
        self.st = state if state else self._initial()
        self.last_answer: dict | None = None     # the assessment of the most recent answer (stored on the student's turn)

    # ------------------------------------------------------------------ setup
    def _initial(self) -> dict:
        plan = self.ctx["plan"]
        return {
            "v": ENGINE_VERSION, "mode": "idle", "phase": "warmup", "step": "start", "cur": None, "history": [],
            "asked": [], "listen_since": None, "answer_started_at": None, "speaking": False, "silence_stage": 0,
            "think_until": None, "idk_streak": 0, "target_difficulty": plan.get("start_difficulty", 1),
            "counts": {"fundamentals": 0, "depth": 0, "applied": 0}, "depth_topic": None, "ack_i": 0,
            "empty_streak": 0, "flags": {"injection": 0, "offtopic": 0, "cuts": 0, "no_answers": 0, "idk": 0,
                                         "repeats": 0, "hints": 0, "identity": "unchecked"},
            "phase_log": [], "ended": None, "last_say": None, "closing_at": None, "decisions": [],
        }

    # ------------------------------------------------------------------ clock helpers
    def window(self, phase: str) -> dict:
        for p in self.phases:
            if p["id"] == phase:
                return p
        return {"id": phase, "start": 0, "end": HARD_STOP_S}

    def phase_at(self, now: float) -> str:
        for p in self.phases:
            if now < p["end"]:
                return p["id"]
        return "closing"

    def left_in(self, phase: str, now: float) -> float:
        return self.window(phase)["end"] - now

    @property
    def wrapup_start(self) -> float:
        return self.window("wrapup")["start"]

    @property
    def close_at(self) -> float:
        return self.window("wrapup")["end"]

    # ------------------------------------------------------------------ utterances
    def _seg(self, text: str, source: str) -> dict:
        return {"text": text, "source": source}

    def _say(self, segments: list[dict], kind: str, now: float, *, expect: bool = True, code: str | None = None,
             reason: str = "", assessment: dict | None = None, show_code: str | None = None, extra: dict | None = None) -> dict:
        segs = [s for s in segments if s and s.get("text")]
        for s in segs:   # guard: no system or variant line may evaluate the student
            if not s["source"].startswith("bank:") and EVALUATIVE.search(s["text"]) and s["source"] != "system:greeting":
                s["text"], s["source"] = phrases.LINES["move_on"], "system:move_on(guarded)"
        st = self.st
        st["mode"] = "speaking"
        st["listen_since"] = None
        st["silence_stage"] = 0 if kind not in ("prompt", "rephrase") else st["silence_stage"]
        act = {"type": "say", "kind": kind, "segments": segs, "expect": expect, "code": code, "phase": st["phase"],
               "reason": reason, "t": round(now, 2)}
        if assessment is not None:
            act["assessment"] = assessment
        if show_code:
            act["show_code"] = show_code
        if extra:
            act.update(extra)
        if expect:
            st["last_say"] = {"segments": segs, "kind": kind, "code": code}
        st["decisions"].append({"t": round(now, 1), "kind": kind, "code": code, "reason": reason})
        if len(st["decisions"]) > 400:
            st["decisions"] = st["decisions"][-400:]
        return act

    def _ack(self) -> dict:
        self.st["ack_i"] += 1
        return self._seg(phrases.ack(self.st["ack_i"]), "system:ack")

    def _idk_ack(self) -> dict:
        self.st["ack_i"] += 1
        return self._seg(phrases.idk_ack(self.st["ack_i"]), "system:idk_ack")

    def _line(self, key: str) -> dict:
        return self._seg(phrases.LINES[key], f"system:{key}")

    def _question_segment(self, q: dict) -> dict:
        return self._seg(q["question"], f"bank:{q['code']}")

    # ------------------------------------------------------------------ events
    def start(self, now: float) -> list[dict]:
        st = self.st
        st["step"] = "identity"
        st["phase"] = "warmup"
        st["phase_log"].append({"phase": "warmup", "t": round(now, 1)})
        text = phrases.greeting(_first_name(self.ctx.get("student_name", "")), self.ctx.get("subject", "this subject"))
        return [self._say([self._seg(text, "system:greeting")], "prompt", now, reason="Start: greeting and identity check")]

    def playback_done(self, now: float) -> list[dict]:
        st = self.st
        if st["mode"] == "ended":
            return []
        if st["step"] == "closing":
            return self._end(now, st.get("end_reason") or "completed")
        if st["mode"] == "speaking":
            st["mode"] = "listening"
            st["listen_since"] = now
        return []

    def speech_started(self, now: float) -> None:
        st = self.st
        st["speaking"] = True
        if st["answer_started_at"] is None:
            st["answer_started_at"] = now
        if st["mode"] == "speaking":     # barge-in: the student started talking over the examiner
            st["mode"] = "listening"
            st["listen_since"] = now

    def speech_stopped(self, now: float) -> None:
        self.st["speaking"] = False

    def repeat_request(self, now: float) -> list[dict]:
        return self._repeat(now, reason="Student pressed repeat")

    def end_early(self, now: float) -> list[dict]:
        if self.st["mode"] == "ended":
            return []
        self._finish_item(now)
        self.st["step"] = "closing"
        self.st["end_reason"] = "ended_by_student"
        return [self._say([self._line("close_early")], "close", now, expect=False, reason="Student chose to end early")]

    def tick(self, now: float) -> list[dict]:
        st = self.st
        if st["mode"] == "ended" or st["step"] == "start":
            return []
        if now >= HARD_STOP_S:
            self._finish_item(now)
            return self._end(now, "time_up")
        if st["step"] == "closing":
            if st.get("closing_at") is not None and now - st["closing_at"] > 25:
                return self._end(now, st.get("end_reason") or "completed")
            return []
        if now >= self.close_at:
            if st["speaking"]:
                return [self._cut_action(now, "Wrap-up time is over")]
            return self._close(now, "close_time", "time_up_close")
        # 90-second cap on any single answer.
        if st["speaking"] and st["answer_started_at"] is not None and now - st["answer_started_at"] >= ANSWER_CAP_S \
                and st["step"] not in ("identity",) and not st.get("cut_pending"):
            return [self._cut_action(now, "Answer reached the 90 second cap")]
        if st["mode"] != "listening" or st["speaking"] or st["listen_since"] is None:
            return []
        if st["think_until"] and now < st["think_until"]:
            return []
        quiet = now - st["listen_since"]
        if st["step"] == "identity":
            if quiet >= 12 and st["silence_stage"] == 0:
                st["silence_stage"] = 1
                return [self._say([self._line("identity_again")], "prompt", now, reason="No identity answer after 12 s")]
            if quiet >= 12 and st["silence_stage"] >= 1:
                st["flags"]["identity"] = "unconfirmed"
                return self._after_identity("", now)
            return []
        if st["step"] == "wrapup_q":
            if quiet >= 15:
                return self._close(now, "close", "No addition in wrap-up")
            return []
        stage = st["silence_stage"]
        if stage == 0 and quiet >= SILENCE_PROMPT_S:
            st["silence_stage"] = 1
            act = self._say([self._line("silence_1")], "prompt", now, reason=f"{SILENCE_PROMPT_S} s of silence")
            act["log_integrity"] = {"kind": "long_silence", "severity": "info", "detail": {"seconds": SILENCE_PROMPT_S}}
            return [act]
        if stage == 1 and quiet >= SILENCE_REPHRASE_S - SILENCE_PROMPT_S:
            st["silence_stage"] = 2
            return self._rephrase(now, reason=f"Still silent after {SILENCE_REPHRASE_S} s", lead=[self._line("silence_rephrase")])
        if stage == 2 and quiet >= SILENCE_MOVE_ON_S - SILENCE_REPHRASE_S:
            st["silence_stage"] = 3
            st["flags"]["no_answers"] += 1
            if st["cur"]:
                st["cur"]["answers"].append({"label": "no_answer", "text": "", "t": round(now, 1), "coverage": 0.0})
            act = self._advance(now, [self._line("silence_move_on")], reason=f"No answer after {SILENCE_MOVE_ON_S} s")
            for a in act:
                if a.get("type") == "say":
                    a["log_integrity"] = {"kind": "long_silence", "severity": "warn", "detail": {"seconds": SILENCE_MOVE_ON_S}}
                    break
            return act
        return []

    def _cut_action(self, now: float, reason: str) -> dict:
        self.st["cut_pending"] = True
        self.st["flags"]["cuts"] += 1
        act = self._say([self._line("cut")], "prompt", now, expect=False, reason=reason)
        act["type"] = "cut"
        return act

    # ------------------------------------------------------------------ answers
    def answer(self, text: str, now: float, meta: dict | None = None) -> list[dict]:
        st = self.st
        meta = meta or {}
        if st["mode"] == "ended" or st["step"] in ("closing", "start"):
            return []
        started = st["answer_started_at"] if st["answer_started_at"] is not None else now
        st["answer_started_at"] = None
        st["speaking"] = False
        cut = bool(meta.get("cut") or st.pop("cut_pending", False))
        if st["step"] == "identity":
            self.last_answer = {"kind": "identity", "label": "identity", "t": round(started, 1)}
            return self._after_identity(text, now)
        if st["step"] == "wrapup_q":
            self.last_answer = {"kind": "wrapup", "label": "wrapup", "t": round(started, 1)}
            return self._after_wrapup(text, now)
        cur = st["cur"]
        if cur is None:
            return self._advance(now, [], reason="No current question")
        q = self.q[cur["code"]]
        fu = q["follow_ups"][cur["fu"]] if cur["kind"] == "followup" and cur.get("fu") is not None else None
        generic = cur["kind"] == "followup" and fu is None
        if generic:
            kp, qtext = [], phrases.LINES["probe_why"]
        elif fu is not None:
            kp = fu.get("key_points") or [q["key_points"][i] for i in range(len(q["key_points"])) if i not in cur["covered"]]
            qtext = fu["text"]
        else:
            kp, qtext = q["key_points"], q["question"]
        a = assess(text, key_points=kp, question_text=qtext + " " + q["question"], vocab=self.ctx.get("vocab", set()),
                   aliases=self.ctx.get("aliases"), misconceptions=q.get("misconceptions"),
                   is_project=(q["type"] == "project" and fu is None and not generic), generic_probe=generic)
        record = {**a.to_dict(), "text": text[:4000], "t": round(started, 1), "t_end": round(now, 1),
                  "duration": round(now - started, 1), "kind": cur["kind"], "fu": cur.get("fu"),
                  "after_hint": cur.get("awaiting_hint", False), "cut": cut}
        if not fu and not generic and a.covered:
            cur["covered"] = sorted(set(cur["covered"]) | set(a.covered))
        cur["answers"].append(record)
        self.last_answer = {**record, "code": cur["code"], "phase": cur["phase"], "generic": generic,
                            "topic": cur["topic"]}
        self.last_answer.pop("text", None)
        actions: list[dict] = []
        if a.injection:
            st["flags"]["injection"] += 1
            actions.append({"type": "flag", "kind": "injection", "severity": "warn",
                            "detail": {"text": text[:300], "code": cur["code"]}})
        label = a.label
        ad = a.to_dict()
        if label != "empty":
            st["empty_streak"] = 0
        if cut:
            return actions + self._advance(now, [], reason="Answer cut at the time cap; moving on", assessment=ad)
        if now >= self.wrapup_start:
            return actions + self._advance(now, [self._ack()], reason="Wrap-up time reached", assessment=ad)
        if label == "empty":
            st["empty_streak"] += 1
            if st["empty_streak"] >= 3:
                st["empty_streak"] = 0
                return actions + self._advance(now, [self._line("move_on")], reason="Could not hear an answer three times", assessment=ad)
            if st["empty_streak"] == 2:
                return actions + self._repeat(now, reason="Nothing usable heard twice: repeat the question")
            return actions + [self._say([self._line("didnt_catch")], "prompt", now, reason="Nothing usable heard", assessment=ad,
                                        extra={"replay_previous": True})]
        if label == "repeat":
            st["flags"]["repeats"] += 1
            cur["repeats"] = cur.get("repeats", 0) + 1
            if cur["repeats"] > 2:
                return actions + self._rephrase(now, reason="Asked to repeat more than twice: rephrase instead")
            return actions + self._repeat(now, reason="Student asked for a repeat")
        if label == "clarify":
            return actions + self._rephrase(now, reason="Student asked what the question means")
        if label == "think":
            st["think_until"] = now + 25
            return actions + [self._say([self._line("think")], "prompt", now, reason="Student asked for time to think",
                                        extra={"keep_question": True})]
        st["think_until"] = None
        if label == "idk":
            st["flags"]["idk"] += 1
            st["idk_streak"] += 1
            st["target_difficulty"] = max(1, st["target_difficulty"] - 1)
            lead = [self._idk_ack()]
            if st["idk_streak"] == 2:
                lead.append(self._line("different_area"))
            return actions + self._advance(now, lead, reason="Student said they don't know: move on, easier next question",
                                           assessment=ad, avoid_topic=q["topic"])
        if label == "offtopic":
            st["flags"]["offtopic"] += 1
            if cur.get("redirects", 0) == 0:
                cur["redirects"] = 1
                key = "redirect_injection" if a.injection else "redirect"
                return actions + [self._say([self._line(key), *self._current_segments()], "redirect", now, code=cur["code"],
                                            reason="Off-topic answer: redirect once", assessment=ad)]
            return actions + self._advance(now, [self._ack()], reason="Off-topic again: move on", assessment=ad)
        st["idk_streak"] = 0
        if label == "strong":
            if not cur.get("awaiting_hint"):
                st["target_difficulty"] = min(3, st["target_difficulty"] + 1)
            nxt = self._pick_followup(q, cur, text, now)
            if nxt is not None and not cur.get("awaiting_hint"):
                return actions + self._ask_followup(q, cur, nxt, now, ad,
                                                    reason=f"Strong answer (coverage {a.coverage:.2f}): go deeper")
            return actions + self._advance(now, [self._ack()], reason=f"Strong answer (coverage {a.coverage:.2f}); "
                                           f"{'after hint' if cur.get('awaiting_hint') else 'follow-ups used or no time'}: move on",
                                           assessment=ad)
        # partial / weak
        if not cur.get("hint_used"):
            hint = self._pick_hint(q, cur, a)
            if hint is not None:
                cur["hint_used"] = True
                cur["awaiting_hint"] = True
                st["flags"]["hints"] += 1
                text_, source = hint
                return actions + [self._say([self._line("hint_intro"), self._seg(text_, source)], "hint", now, code=cur["code"],
                                            reason=f"{label.capitalize()} answer (coverage {a.coverage:.2f}): one hint",
                                            assessment=ad)]
        return actions + self._advance(now, [self._ack()], reason=f"{label.capitalize()} answer "
                                       f"({'after the hint' if cur.get('hint_used') else 'no hint available'}): move on",
                                       assessment=ad)

    # ------------------------------------------------------------------ identity, wrap-up, closing
    def _after_identity(self, text: str, now: float) -> list[dict]:
        st = self.st
        if text:
            st["flags"]["identity"] = self._check_identity(text)
            st.setdefault("identity_text", text[:300])
        actions: list[dict] = []
        if st["flags"]["identity"] == "mismatch":
            actions.append({"type": "flag", "kind": "identity_mismatch", "severity": "warn",
                            "detail": {"heard": text[:200], "expected_roll": self.ctx.get("roll_no")}})
        opener = self.ctx["plan"].get("opener")
        if opener and opener in self.q:
            q = self.q[opener]
            st["step"] = "main"
            st["cur"] = self._new_item(q, now)
            return actions + [self._say([self._line("rules"), self._question_segment(q)], "question", now, code=q["code"],
                                        reason=f"Identity {st['flags']['identity']}; rules; project opener")]
        st["step"] = "main"
        return actions + self._advance(now, [self._line("rules")], reason="Identity done; no opener in bank")

    def _check_identity(self, text: str) -> str:
        roll = re.sub(r"[^a-z0-9]", "", (self.ctx.get("roll_no") or "").lower())
        heard = re.sub(r"[^a-z0-9]", "", normalize(text))
        digits_roll = re.sub(r"\D", "", roll)
        digits_heard = re.sub(r"\D", "", heard)
        name_tokens = [t for t in tokens(normalize(self.ctx.get("student_name", ""))) if len(t) >= 3]
        name_hit = any(t in tokens(normalize(text)) for t in name_tokens)
        if roll and (roll in heard or (len(digits_roll) >= 3 and digits_roll[-3:] in digits_heard)):
            return "confirmed"
        if name_hit:
            return "name_only"
        if len(digits_heard) >= 4 and digits_roll and digits_heard[-3:] != digits_roll[-3:]:
            return "mismatch"
        return "unconfirmed"

    def _after_wrapup(self, text: str, now: float) -> list[dict]:
        st = self.st
        st["history"].append({"code": "wrapup", "topic": "Wrap-up", "phase": "wrapup", "type": "wrapup",
                              "answers": [{"text": text[:4000], "t": round(now, 1), "label": "wrapup"}]})
        return self._close(now, "close", "Wrap-up answer received")

    def _close(self, now: float, line: str, reason: str) -> list[dict]:
        st = self.st
        self._finish_item(now)
        st["step"] = "closing"
        st["closing_at"] = now
        st["phase"] = "wrapup"
        st["end_reason"] = "completed" if line == "close" else "time_up"
        return [self._say([self._line(line)], "close", now, expect=False, reason=reason)]

    def _end(self, now: float, reason: str) -> list[dict]:
        st = self.st
        self._finish_item(now)
        st["mode"] = "ended"
        st["ended"] = {"t": round(now, 1), "reason": reason}
        return [{"type": "end", "reason": reason, "t": round(now, 1)}]

    # ------------------------------------------------------------------ items
    def _new_item(self, q: dict, now: float) -> dict:
        st = self.st
        st["asked"].append(q["code"])
        st["phase"] = q["phase"] if q["phase"] != "warmup" else "warmup"
        return {"code": q["code"], "topic": q["topic"], "phase": q["phase"], "type": q["type"], "kind": "question",
                "fu": None, "followups": [], "covered": [], "answers": [], "hint_used": False, "awaiting_hint": False,
                "redirects": 0, "repeats": 0, "rephrased": False, "asked_at": round(now, 1)}

    def _finish_item(self, now: float) -> None:
        st = self.st
        cur = st["cur"]
        if not cur:
            return
        q = self.q.get(cur["code"], {})
        main_answers = [a for a in cur["answers"] if a.get("kind") == "question" and a.get("label") not in ("repeat", "clarify", "think", "empty")]
        best = max((a.get("coverage", 0.0) for a in main_answers), default=0.0)
        st["history"].append({
            "code": cur["code"], "topic": cur["topic"], "phase": cur["phase"], "type": cur["type"],
            "difficulty": q.get("difficulty"), "asked_at": cur["asked_at"], "ended_at": round(now, 1),
            "best_coverage": round(best, 3), "covered": cur["covered"], "n_key_points": len(q.get("key_points", [])),
            "hint_used": cur["hint_used"], "followups": cur["followups"], "answers": cur["answers"],
            "labels": [a.get("label") for a in cur["answers"]],
        })
        st["cur"] = None

    def _current_segments(self) -> list[dict]:
        last = self.st.get("last_say") or {}
        segs = [s for s in last.get("segments", []) if s["source"].startswith(("bank:", "variant:", "system:probe"))]
        if segs:
            return segs[-1:]
        cur = self.st["cur"]
        if cur:
            return [self._question_segment(self.q[cur["code"]])]
        return []

    def _repeat(self, now: float, reason: str) -> list[dict]:
        segs = self._current_segments()
        cur = self.st["cur"]
        if not segs:
            return []
        return [self._say([self._line("repeat_intro"), *segs], "repeat", now, code=cur["code"] if cur else None, reason=reason)]

    def _rephrase(self, now: float, reason: str, lead: list[dict] | None = None) -> list[dict]:
        cur = self.st["cur"]
        if cur is None:
            return []
        q = self.q[cur["code"]]
        lead = lead or []
        if cur["kind"] == "question" and q.get("rephrase") and not cur.get("rephrased"):
            cur["rephrased"] = True
            return [self._say([*lead, self._seg(q["rephrase"], f"bank:{q['code']}/r")], "rephrase", now, code=q["code"],
                              reason=reason)]
        return [self._say([*lead, *self._current_segments()], "repeat", now, code=q["code"], reason=reason + " (no rephrasing left)")]

    def _pick_hint(self, q: dict, cur: dict, a: Assessment) -> tuple[str, str] | None:
        if a.misconception and a.misconception_hint:
            return a.misconception_hint, f"bank:{q['code']}/m"
        if cur["kind"] == "question" and q.get("hints"):
            return q["hints"][0], f"bank:{q['code']}/h1"
        return None

    def _pick_followup(self, q: dict, cur: dict, answer_text: str, now: float) -> int | dict | None:
        phase = cur["phase"]
        if len(cur["followups"]) >= MAX_FOLLOWUPS.get(phase, 1):
            return None
        if self.left_in(phase if phase != "warmup" else "fundamentals", now) < MIN_LEFT_FOR_FOLLOWUP.get(phase, 25):
            return None
        if phase == "warmup" and now > self.window("warmup")["end"] + 20:
            return None
        used = {f["fu"] for f in cur["followups"] if isinstance(f.get("fu"), int)}
        cands = [i for i in range(len(q.get("follow_ups", []))) if i not in used]
        if cands:
            toks = tokens(normalize(answer_text, self.ctx.get("aliases")))
            matched = [i for i in cands if any(phrase_in(term_tokens(w), toks) for w in q["follow_ups"][i].get("when", []))]
            if matched:
                return matched[0]
            pref = FOLLOWUP_PREFERENCE.get(phase, FOLLOWUP_PREFERENCE["fundamentals"])
            cands.sort(key=lambda i: pref.index(q["follow_ups"][i].get("kind", "deeper"))
                       if q["follow_ups"][i].get("kind", "deeper") in pref else 9)
            return cands[0]
        # Bank follow-ups are used up. Fall back to one generic probe, at most once per kind per viva, and only for
        # questions that had no follow-ups of their own (otherwise move on and cover more of the syllabus).
        if q.get("follow_ups") and q["type"] != "project":
            return None
        if phase not in ("depth", "applied") and q["type"] != "project":
            return None
        used_generic = self.st.setdefault("generic_used", [])
        for generic in (["probe_project"] if q["type"] == "project" else ["probe_why", "probe_example"]):
            if generic not in used_generic:
                used_generic.append(generic)
                return {"generic": generic}
        return None

    def _ask_followup(self, q: dict, cur: dict, nxt: int | dict, now: float, ad: dict, reason: str) -> list[dict]:
        cur["kind"] = "followup"
        cur["awaiting_hint"] = False
        if isinstance(nxt, dict):
            key = nxt["generic"]
            cur["fu"] = None
            cur["followups"].append({"fu": key, "kind": key.replace("probe_", ""), "t": round(now, 1)})
            seg = self._line(key)
            return [self._say([self._ack(), seg], "followup", now, code=q["code"], reason=reason + f" (generic {key})",
                              assessment=ad)]
        fu = q["follow_ups"][nxt]
        cur["fu"] = nxt
        cur["followups"].append({"fu": nxt, "kind": fu.get("kind", "deeper"), "t": round(now, 1)})
        act = self._say([self._ack(), self._seg(fu["text"], f"bank:{q['code']}/f{nxt + 1}")], "followup", now, code=q["code"],
                        reason=reason + f" (follow-up f{nxt + 1}, {fu.get('kind', 'deeper')})", assessment=ad)
        act["variant_request"] = {"base": fu["text"], "source": f"bank:{q['code']}/f{nxt + 1}", "question": q["question"],
                                  "answer": (cur["answers"][-1]["text"] if cur["answers"] else "")[:1200],
                                  "key_points": [kp["point"] for kp in (fu.get("key_points") or q["key_points"])]}
        return [act]

    # ------------------------------------------------------------------ choosing what comes next
    def _advance(self, now: float, lead: list[dict], reason: str, assessment: dict | None = None,
                 avoid_topic: str | None = None) -> list[dict]:
        st = self.st
        prev_phase = st["phase"]
        self._finish_item(now)
        st["think_until"] = None
        if now >= self.wrapup_start or now >= self.close_at - 40:
            return self._go_wrapup(now, lead, reason, assessment)
        phase = self.phase_at(now)
        if phase == "warmup":
            phase = "fundamentals"           # the opener is done; fundamentals start now even if a little early
        order = ["fundamentals", "depth", "applied"]
        for ph in order[order.index(phase):]:
            q = self._choose(ph, now, avoid_topic)
            if q is not None:
                lead2 = list(lead)
                if ph != prev_phase:
                    st["phase_log"].append({"phase": ph, "t": round(now, 1)})
                    if ph == "fundamentals":
                        lead2.append(self._line("to_fundamentals"))
                    elif ph == "depth":
                        lead2.append(self._seg(phrases.depth_intro(q["topic"]), "system:depth_intro"))
                    elif ph == "applied":
                        lead2.append(self._line("to_applied_code" if q.get("code_snippet") else "to_applied"))
                st["cur"] = self._new_item(q, now)
                st["cur"]["phase"] = ph
                st["phase"] = ph
                st["counts"][ph] += 1
                why = reason
                if ph == "depth" and st.get("depth_reason"):
                    why += f"; {st['depth_reason']}"
                if st.get("choose_note"):
                    why += f"; {st['choose_note']}"
                if ph != self.phase_at(now) and self.phase_at(now) != "warmup":
                    why += f"; {self.phase_at(now)} questions used up, so {ph} starts {int(self.window(ph)['start'] - now)} s early"
                return [self._say([*lead2, self._question_segment(q)], "question", now, code=q["code"],
                                  reason=f"{why} -> {ph} question {q['code']} (difficulty {q['difficulty']})",
                                  assessment=assessment, show_code=q.get("code_snippet") or None)]
        return self._go_wrapup(now, lead, reason + "; question bank exhausted", assessment)

    def _go_wrapup(self, now: float, lead: list[dict], reason: str, assessment: dict | None) -> list[dict]:
        st = self.st
        lead = [s for s in lead if s["source"] not in ("system:different_area",)]
        st["step"] = "wrapup_q"
        st["phase"] = "wrapup"
        st["phase_log"].append({"phase": "wrapup", "t": round(now, 1)})
        return [self._say([*lead, self._line("to_wrapup"), self._line("wrapup_q")], "wrapup", now, reason=reason + " -> wrap-up",
                          assessment=assessment)]

    def _choose(self, phase: str, now: float, avoid_topic: str | None) -> dict | None:
        st = self.st
        plan = self.ctx["plan"]
        n = st["counts"][phase]
        left = self.left_in(phase, now)
        if n >= MAX_MAIN[phase] or left < MIN_LEFT_FOR_QUESTION[phase]:
            return None
        if n >= QUOTA[phase] and left < EXTRA_NEEDS_S[phase]:
            return None          # quota met and not enough time for another: let the next phase start a little early
        asked = set(st["asked"])
        st.pop("choose_note", None)
        if n >= QUOTA[phase]:
            st["choose_note"] = f"{phase} quota met but {int(left)} s remain in the phase: one more question"
        if phase == "fundamentals":
            return self._pick_fundamental(asked, avoid_topic)
        if phase == "depth":
            struggling = st["target_difficulty"] <= 1 and sum(1 for h in st["history"] if "idk" in h["labels"]) >= 2
            if st["depth_topic"] is None:
                st["depth_topic"], st["depth_reason"] = self._strongest_topic()
            topic = st["depth_topic"]
            pool = [c for c in plan["depth"].get(topic, []) if c in self.q and c not in asked]
            if not pool:
                for t, sc in self._topic_ranking():
                    pool = [c for c in plan["depth"].get(t, []) if c in self.q and c not in asked]
                    if pool:
                        st["depth_topic"] = t
                        st["depth_reason"] = f"depth topic {t}: next strongest (score {sc:.2f})"
                        break
            if not pool:
                pool = [c for cs in plan["depth"].values() for c in cs if c in self.q and c not in asked]
                if pool:
                    st["depth_topic"] = self.q[pool[0]]["topic"]
                    st["depth_reason"] = f"depth topic {st['depth_topic']}: stronger topics have no depth questions left"
            if struggling:
                easy = [c for c in pool if self.q[c]["difficulty"] <= 2]
                if easy:
                    pool = easy
                else:
                    q = self._pick_fundamental(asked, avoid_topic)
                    if q is not None:
                        st["depth_reason"] = "student is struggling: an easier bank question instead of a hard depth question"
                        return q
            return self.q[pool[0]] if pool else None
        if phase == "applied":
            cands = [self.q[c] for c in plan["applied"] if c in self.q and c not in asked]
            if not cands:
                return None
            strong = {t for t, s in self._topic_ranking()[:2]}
            target = st["target_difficulty"]
            cands.sort(key=lambda q: (0 if q["topic"] in strong else 1, abs(q["difficulty"] - max(2, target)),
                                      plan["applied"].index(q["code"])))
            return cands[0]
        return None

    def _pick_fundamental(self, asked: set, avoid_topic: str | None) -> dict | None:
        st = self.st
        plan = self.ctx["plan"]
        cands = [self.q[c] for c in plan["fundamentals"] if c in self.q and c not in asked]
        if not cands:
            return None
        target = st["target_difficulty"]
        recent_topics = [h["topic"] for h in st["history"][-2:]]

        def score(q):
            return (abs(q["difficulty"] - target) * 2 + (3 if q["topic"] == avoid_topic else 0)
                    + (1 if q["topic"] in recent_topics else 0), plan["fundamentals"].index(q["code"]))
        return min(cands, key=score)

    def _topic_ranking(self) -> list[tuple[str, float]]:
        scores: dict[str, list[float]] = {}
        for h in self.st["history"]:
            if h["phase"] not in ("fundamentals", "depth") or h["type"] == "project":
                continue
            fu_bonus = 0.15 * sum(1 for a in h["answers"] if a.get("kind") == "followup" and a.get("label") == "strong")
            idk_pen = 0.2 if "idk" in h["labels"] else 0.0
            scores.setdefault(h["topic"], []).append(h["best_coverage"] + fu_bonus - idk_pen)
        ranked = sorted(((t, sum(v) / len(v)) for t, v in scores.items()), key=lambda x: -x[1])
        return ranked

    def _strongest_topic(self) -> tuple[str | None, str]:
        plan = self.ctx["plan"]
        for t, s in self._topic_ranking():
            if plan["depth"].get(t):
                return t, f"depth topic {t}: strongest so far (score {s:.2f})"
        topics = [t for t, cs in plan["depth"].items() if cs]
        if not topics:
            return None, "no depth questions"
        return topics[0], f"depth topic {topics[0]}: no fundamentals evidence yet, first available"

    # ------------------------------------------------------------------ summary for scoring
    def summary(self) -> dict:
        st = self.st
        return {"history": st["history"], "flags": st["flags"], "phase_log": st["phase_log"], "ended": st["ended"],
                "depth_topic": st["depth_topic"], "decisions": st["decisions"], "engine": ENGINE_VERSION}
