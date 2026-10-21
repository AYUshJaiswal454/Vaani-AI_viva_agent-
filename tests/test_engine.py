"""The examiner: phases on one clock, follow-up policy, time caps, silence handling, traceability, neutrality."""
from __future__ import annotations

import re
import unittest

from tests.personas import PERSONAS
from tests.sim import run_viva
from tests.support import approved_questions

from app.bank.models import bank_vocabulary
from app.engine.examiner import (ANSWER_CAP_S, DEFAULT_PHASES, Examiner, make_plan, validate_phase_plan)
from app.engine.text import EVALUATIVE

QS, ALIASES = approved_questions("dbms")
QMAP = {q["code"]: q for q in QS}


def viva(persona: str, seed: int = 1, **kw):
    return run_viva(QS, PERSONAS[persona](seed=seed), aliases=ALIASES, seed=f"test:{persona}:{seed}", **kw)


def examiner(seed: str = "unit") -> Examiner:
    ctx = {"student_name": "Asha Verma", "roll_no": "22CS1047", "subject": "DBMS", "plan": make_plan(QS, seed),
           "vocab": bank_vocabulary(QS, ALIASES), "aliases": ALIASES}
    return Examiner(None, QMAP, ctx)


def says(acts):
    return [a for a in acts if a["type"] in ("say", "cut")]


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {p: [viva(p, s) for s in range(3)] for p in PERSONAS}

    def test_every_viva_finishes_within_the_hard_limit(self):
        for p, runs in self.runs.items():
            for r in runs:
                self.assertIsNotNone(r["ended"], p)
                self.assertLessEqual(r["ended"]["t"], 900, p)

    def test_phases_run_in_order_and_wrap_up_starts_by_13_minutes(self):
        order = ["warmup", "fundamentals", "depth", "applied", "wrapup"]
        for p, runs in self.runs.items():
            for r in runs:
                log = [x["phase"] for x in r["summary"]["phase_log"]]
                self.assertEqual(log, [x for x in order if x in log], (p, log))
                self.assertEqual(log[0], "warmup")
                self.assertEqual(log[-1], "wrapup", p)
                wrap = next(x["t"] for x in r["summary"]["phase_log"] if x["phase"] == "wrapup")
                self.assertLessEqual(wrap, 780 + ANSWER_CAP_S + 30, p)     # at most one capped answer past 13:00

    def test_strong_students_get_followups_and_the_full_flow(self):
        for r in self.runs["strong"]:
            kinds = [t["kind"] for t in r["transcript"] if t["speaker"] == "examiner"]
            self.assertGreaterEqual(kinds.count("followup"), 5)
            phases = {x["phase"] for x in r["summary"]["phase_log"]}
            self.assertEqual(phases, set(o["id"] for o in DEFAULT_PHASES))
            self.assertGreater(r["duration"], 600, "a strong student should use most of the 15 minutes")

    def test_partial_answer_gets_exactly_one_hint_then_moves_on(self):
        for r in self.runs["weak"] + self.runs["average"]:
            for h in r["summary"]["history"]:
                hints = sum(1 for a in h["answers"] if a.get("after_hint"))
                self.assertLessEqual(hints, 1, h["code"])
            per_code = {}
            for t in r["transcript"]:
                if t["speaker"] == "examiner" and t["kind"] == "hint":
                    per_code[t["code"]] = per_code.get(t["code"], 0) + 1
            self.assertTrue(all(n == 1 for n in per_code.values()), per_code)

    def test_i_dont_know_moves_on_without_hints_or_followups(self):
        for r in self.runs["idk"]:
            kinds = [t["kind"] for t in r["transcript"] if t["speaker"] == "examiner" and t.get("code") and t["phase"] != "warmup"]
            self.assertNotIn("followup", kinds)
            self.assertNotIn("hint", kinds)
            # difficulty steps down: later questions are never harder than the first one after an IDK streak
            diffs = [QMAP[t["code"]]["difficulty"] for t in r["transcript"] if t["speaker"] == "examiner" and t["kind"] == "question"
                     and t.get("code") in QMAP and QMAP[t["code"]]["phase"] == "fundamentals"]
            self.assertTrue(all(d <= 2 for d in diffs), diffs)

    def test_offtopic_is_redirected_once_per_question(self):
        for r in self.runs["offtopic"]:
            red = [t["code"] for t in r["transcript"] if t["speaker"] == "examiner" and t["kind"] == "redirect"]
            self.assertTrue(red, "expected at least one redirect")
            self.assertEqual(len(red), len(set(red)), "never redirect twice for the same question")

    def test_rambling_answers_are_cut_at_90_seconds(self):
        for r in self.runs["rambler"]:
            cuts = [t for t in r["transcript"] if t["speaker"] == "student" and t.get("cut")]
            self.assertTrue(cuts)
            for c in cuts:
                self.assertLessEqual(c["t_end"] - c["t"], ANSWER_CAP_S + 1.5)

    def test_injection_is_flagged_and_ignored(self):
        for r in self.runs["injector"]:
            self.assertTrue(any(f.get("kind") == "injection" for f in r["flags"]))
            for t in r["transcript"]:
                if t["speaker"] == "examiner":
                    self.assertNotRegex(t["text"].lower(), r"full marks|10 out of 10|correct answer is")

    def test_silent_student_is_prompted(self):
        for r in self.runs["silent"]:
            texts = " ".join(t["text"] for t in r["transcript"] if t["speaker"] == "examiner")
            self.assertIn("Take your time", texts)
            self.assertTrue(any(f.get("kind") == "long_silence" for f in r["flags"]))

    def test_every_examiner_line_is_traceable(self):
        for p, runs in self.runs.items():
            for r in runs:
                for t in r["transcript"]:
                    if t["speaker"] != "examiner":
                        continue
                    for src in t["sources"]:
                        self.assertRegex(src, r"^(bank:[A-Z]+-[WFDA]-\d+(/(f\d+|h\d+|r|m))?|system:[a-z0-9_]+(\(guarded\))?|variant:.+)$")
                        if src.startswith("bank:"):
                            self.assertIn(src.split(":")[1].split("/")[0], QMAP)

    def test_examiner_never_evaluates_or_reveals(self):
        for p, runs in self.runs.items():
            for r in runs:
                for t in r["transcript"]:
                    if t["speaker"] != "examiner":
                        continue
                    non_bank = " ".join(seg["text"] for seg in t["segments"] if not seg["source"].startswith("bank:"))
                    self.assertIsNone(EVALUATIVE.search(non_bank), (p, t["text"]))

    def test_each_question_asked_at_most_once(self):
        for p, runs in self.runs.items():
            for r in runs:
                asked = [t["code"] for t in r["transcript"] if t["speaker"] == "examiner" and t["kind"] == "question" and t.get("code")]
                self.assertEqual(len(asked), len(set(asked)), p)

    def test_fundamentals_respect_quota_unless_time_remains(self):
        for r in self.runs["strong"]:
            fund = [t for t in r["transcript"] if t["speaker"] == "examiner" and t["kind"] == "question" and t.get("phase") == "fundamentals"]
            for i, t in enumerate(fund[3:], start=4):
                self.assertIn("quota met", t["reason"])


class Policy(unittest.TestCase):
    def start(self, ex: Examiner) -> None:
        ex.start(0)
        ex.playback_done(8)
        ex.speech_started(9)
        ex.answer("My name is Asha Verma, roll number 22CS1047", 12)
        ex.playback_done(30)

    def test_identity_is_checked(self):
        ex = examiner()
        self.start(ex)
        self.assertEqual(ex.st["flags"]["identity"], "confirmed")
        ex2 = examiner()
        ex2.start(0)
        ex2.playback_done(8)
        acts = ex2.answer("my roll number is 21EC5555", 12)
        self.assertEqual(ex2.st["flags"]["identity"], "mismatch")
        self.assertTrue(any(a["type"] == "flag" and a["kind"] == "identity_mismatch" for a in acts))

    def test_repeat_and_clarify_requests(self):
        ex = examiner()
        self.start(ex)
        q = QMAP[ex.st["cur"]["code"]]
        acts = says(ex.answer("Sorry, can you repeat the question?", 35))
        self.assertEqual(acts[0]["kind"], "repeat")
        self.assertIn(q["question"], " ".join(s["text"] for s in acts[0]["segments"]))
        ex.playback_done(45)
        acts = says(ex.answer("what do you mean by that question?", 50))
        self.assertIn(acts[0]["kind"], ("rephrase", "repeat"))

    def test_hindi_i_dont_know(self):
        ex = examiner()
        self.start(ex)
        ex.answer("I built a library app in Java with MySQL and I designed the tables myself", 50)
        ex.playback_done(60)
        acts = says(ex.answer("sir mujhe pata nahi", 66))
        self.assertIn(acts[0]["segments"][0]["source"], ("system:idk_ack",))

    def test_think_request_suppresses_silence_prompts(self):
        ex = examiner()
        self.start(ex)
        acts = says(ex.answer("let me think", 33))
        self.assertEqual(acts[0]["segments"][0]["text"], "Sure, take your time.")
        ex.playback_done(35)
        for t in range(36, 58):
            self.assertEqual(says(ex.tick(t)), [], t)

    def test_silence_ladder(self):
        ex = examiner()
        self.start(ex)                                      # listening from t=30
        self.assertEqual(says(ex.tick(39)), [])
        a = says(ex.tick(40.5))
        self.assertEqual(a[0]["segments"][0]["source"], "system:silence_1")
        ex.playback_done(44)
        a = says(ex.tick(44 + 12.5))
        self.assertIn(a[0]["kind"], ("rephrase", "repeat"))
        self.assertEqual(a[0]["segments"][0]["source"], "system:silence_rephrase")
        ex.playback_done(62)
        a = says(ex.tick(62 + 13.5))
        self.assertEqual(a[0]["segments"][0]["source"], "system:silence_move_on")
        self.assertEqual(a[0]["kind"], "question")       # moved on to the next question

    def test_silence_ladder_moves_on_when_no_rephrasing_is_left(self):
        """Found by the muted-microphone robustness run: repeating verbatim must not restart the ladder forever."""
        ex = examiner()
        self.start(ex)
        ex.st["cur"]["rephrased"] = True
        says(ex.tick(40.5))
        ex.playback_done(44)
        a = says(ex.tick(44 + 12.5))
        self.assertEqual(a[0]["kind"], "repeat")
        ex.playback_done(62)
        a = says(ex.tick(62 + 13.5))
        self.assertEqual(a[0]["segments"][0]["source"], "system:silence_move_on")

    def test_cut_at_answer_cap(self):
        ex = examiner()
        self.start(ex)
        ex.speech_started(31)
        self.assertEqual(says(ex.tick(31 + ANSWER_CAP_S - 1)), [])
        acts = ex.tick(31 + ANSWER_CAP_S + 0.1)
        self.assertEqual(acts[0]["type"], "cut")

    def test_hard_stop(self):
        ex = examiner()
        self.start(ex)
        acts = ex.tick(900.5)
        self.assertEqual(acts[-1]["type"], "end")
        self.assertEqual(acts[-1]["reason"], "time_up")

    def test_strong_followup_prefers_what_the_student_mentioned(self):
        q = QMAP["DBMS-F-03"]
        ex = examiner()
        self.start(ex)
        ex.st["cur"] = ex._new_item(q, 40)
        acts = says(ex.answer("Atomicity means all or nothing, consistency keeps constraints valid, isolation means "
                              "concurrent transactions do not interfere and durability means committed data survives a crash",
                              80))
        self.assertEqual(acts[0]["kind"], "followup")
        src = acts[0]["segments"][-1]["source"]
        self.assertTrue(src.endswith("/f1") or src.endswith("/f2"))

    def test_resume_from_saved_state(self):
        ex = examiner()
        self.start(ex)
        state = ex.st
        import json

        restored = Examiner(json.loads(json.dumps(state)), QMAP, ex.ctx)
        self.assertEqual(restored.st["cur"]["code"], ex.st["cur"]["code"])
        acts = says(restored.repeat_request(40))
        self.assertTrue(acts and acts[0]["kind"] == "repeat")


class Plans(unittest.TestCase):
    def test_plans_are_deterministic_and_differ_between_students(self):
        a1, a2 = make_plan(QS, "viva-1:A"), make_plan(QS, "viva-1:A")
        b = make_plan(QS, "viva-1:B")
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1["fundamentals"][:3], b["fundamentals"][:3])

    def test_fundamentals_spread_across_topics(self):
        p = make_plan(QS, "viva-9:X")
        first = [QMAP[c]["topic"] for c in p["fundamentals"][:4]]
        self.assertEqual(len(set(first)), 4)

    def test_phase_plan_validation(self):
        self.assertEqual(validate_phase_plan(None), DEFAULT_PHASES)
        bad = [dict(p) for p in DEFAULT_PHASES]
        bad[4]["end"] = 895
        with self.assertRaises(ValueError):
            validate_phase_plan(bad)
        gap = [dict(p) for p in DEFAULT_PHASES]
        gap[2]["start"] = 310
        with self.assertRaises(ValueError):
            validate_phase_plan(gap)
        with self.assertRaises(ValueError):
            validate_phase_plan(list(reversed(DEFAULT_PHASES)))


if __name__ == "__main__":
    unittest.main()
