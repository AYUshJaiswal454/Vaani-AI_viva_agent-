"""Question-bank format, import (JSON or CSV), export and validation.

A question carries everything the examiner needs to run a fair, adaptive viva without inventing anything:
key points (what a complete answer covers, with the words students actually use), follow-ups ("why" / "what if"),
hints that nudge without giving the answer away, and common misconceptions. See docs/QUESTION_BANK.md.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..engine.text import content_tokens, normalize, phrase_in, term_tokens, tokens

PHASES = ("warmup", "fundamentals", "depth", "applied")
QTYPES = ("concept", "scenario", "debug", "project")


class KeyPoint(BaseModel):
    point: str = Field(min_length=2, max_length=300)
    terms: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0.1, le=5)

    @model_validator(mode="after")
    def _default_terms(self):
        self.terms = [t.strip() for t in self.terms if t and t.strip()]
        if not self.terms:
            head = re.split(r"[:\-–(]", self.point, maxsplit=1)[0]
            self.terms = [head.strip()]
        return self


class FollowUp(BaseModel):
    text: str = Field(min_length=5, max_length=500)
    kind: Literal["why", "what_if", "deeper", "example", "probe"] = "deeper"
    key_points: list[KeyPoint] = Field(default_factory=list)
    when: list[str] = Field(default_factory=list)       # ask this one first if the answer mentions any of these


class Misconception(BaseModel):
    text: str = Field(min_length=3, max_length=300)
    terms: list[str] = Field(default_factory=list)
    hint: str = ""


class Question(BaseModel):
    code: str = ""
    topic: str = Field(min_length=1, max_length=80)
    phase: Literal["warmup", "fundamentals", "depth", "applied"] = "fundamentals"
    type: Literal["concept", "scenario", "debug", "project"] = "concept"
    difficulty: int = Field(default=2, ge=1, le=3)
    question: str = Field(min_length=8, max_length=1200)
    rephrase: str = ""
    key_points: list[KeyPoint] = Field(default_factory=list)
    follow_ups: list[FollowUp] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    misconceptions: list[Misconception] = Field(default_factory=list)
    code_snippet: str = ""
    expected_seconds: int = Field(default=60, ge=15, le=240)
    tags: list[str] = Field(default_factory=list)

    @field_validator("difficulty", mode="before")
    @classmethod
    def _difficulty(cls, v: Any) -> int:
        if isinstance(v, str):
            return {"easy": 1, "medium": 2, "hard": 3, "1": 1, "2": 2, "3": 3}.get(v.strip().lower(), 2)
        return v

    @field_validator("phase", mode="before")
    @classmethod
    def _phase(cls, v: Any) -> str:
        v = str(v or "fundamentals").strip().lower().replace("-", "").replace(" ", "")
        return {"warmup": "warmup", "opener": "warmup", "fundamental": "fundamentals", "fundamentals": "fundamentals",
                "basics": "fundamentals", "depth": "depth", "deep": "depth", "applied": "applied",
                "appliedproblem": "applied", "scenario": "applied", "problem": "applied"}.get(v, v)

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, v: Any) -> str:
        return str(v or "concept").strip().lower()


class Bank(BaseModel):
    name: str
    subject: str
    description: str = ""
    aliases: dict[str, str] = Field(default_factory=dict)
    questions: list[Question]


# ----------------------------------------------------------------------------- CSV
CSV_COLUMNS = ["code", "topic", "phase", "type", "difficulty", "question", "rephrase", "key_points", "follow_ups",
               "hints", "misconceptions", "code_snippet", "expected_seconds", "tags"]


def _points_from_cell(cell: str) -> list[dict]:
    """'Atomicity: atomic / all or nothing; Durability: durable / survives crash' -> key points."""
    cell = (cell or "").strip()
    if not cell:
        return []
    if cell.startswith("["):
        return json.loads(cell)
    out = []
    for part in re.split(r"\s*;\s*", cell):
        if not part:
            continue
        label, _, terms = part.rpartition(":") if ":" in part else (part, "", "")
        out.append({"point": label.strip(), "terms": [t.strip() for t in terms.split("/") if t.strip()]})
    return out


def _followups_from_cell(cell: str) -> list[dict]:
    """'why|Why is X needed? || what_if|What if Y?' -> follow-ups (kind optional)."""
    cell = (cell or "").strip()
    if not cell:
        return []
    if cell.startswith("["):
        return json.loads(cell)
    out = []
    for part in re.split(r"\s*\|\|\s*", cell):
        kind, sep, text = part.partition("|")
        if not sep:
            kind, text = "deeper", part
        out.append({"text": text.strip(), "kind": kind.strip() or "deeper"})
    return out


def _list_from_cell(cell: str) -> list:
    cell = (cell or "").strip()
    if not cell:
        return []
    if cell.startswith("["):
        return json.loads(cell)
    return [p.strip() for p in re.split(r"\s*\|\|\s*|\s*\|\s*", cell) if p.strip()]


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows = []
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("question"):
            continue
        try:
            rows.append({
                "code": row.get("code", ""), "topic": row.get("topic") or "General", "phase": row.get("phase") or "fundamentals",
                "type": row.get("type") or "concept", "difficulty": row.get("difficulty") or 2, "question": row["question"],
                "rephrase": row.get("rephrase", ""), "key_points": _points_from_cell(row.get("key_points", "")),
                "follow_ups": _followups_from_cell(row.get("follow_ups", "")), "hints": _list_from_cell(row.get("hints", "")),
                "misconceptions": [{"text": m, "terms": [m]} if isinstance(m, str) else m
                                   for m in _list_from_cell(row.get("misconceptions", ""))],
                "code_snippet": row.get("code_snippet", ""), "expected_seconds": int(row.get("expected_seconds") or 60),
                "tags": _list_from_cell(row.get("tags", "")), "_row": i,
            })
        except (ValueError, json.JSONDecodeError) as exc:
            rows.append({"_row": i, "_error": f"row {i}: {exc}"})
    return rows


def _points_cell(points: list[dict]) -> str:
    """Readable 'Point: term / term; ...' when that round-trips exactly, JSON otherwise."""
    simple = all(";" not in k["point"] and all(not any(c in t for c in "/:;") for t in k.get("terms", []))
                 and k.get("weight", 1.0) == 1.0 for k in points)
    if not simple:
        return json.dumps(points, ensure_ascii=False)
    return "; ".join(f"{k['point']}: {' / '.join(k.get('terms', []))}" for k in points)


def _followups_cell(fus: list[dict]) -> str:
    if any(f.get("key_points") or f.get("when") or "|" in f["text"] for f in fus):
        return json.dumps(fus, ensure_ascii=False)
    return " || ".join(f"{f.get('kind', 'deeper')}|{f['text']}" for f in fus)


def to_csv(questions: list[dict]) -> str:
    """One question per row. Simple cells stay readable in a spreadsheet; anything that would not survive the
    readable form exactly (follow-ups with their own key points, '/' inside a term...) is written as JSON."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for q in questions:
        hints = q.get("hints", [])
        w.writerow({
            "code": q["code"], "topic": q["topic"], "phase": q["phase"], "type": q.get("type") or q.get("qtype"),
            "difficulty": q["difficulty"], "question": q.get("question") or q.get("text"), "rephrase": q.get("rephrase", ""),
            "key_points": _points_cell(q.get("key_points", [])),
            "follow_ups": _followups_cell(q.get("follow_ups", [])),
            "hints": json.dumps(hints, ensure_ascii=False) if any("|" in h for h in hints) else " || ".join(hints),
            "misconceptions": json.dumps(q.get("misconceptions", []), ensure_ascii=False) if q.get("misconceptions") else "",
            "code_snippet": q.get("code_snippet", ""), "expected_seconds": q.get("expected_seconds") or q.get("expected_s") or 60,
            "tags": " | ".join(q.get("tags", [])),
        })
    return buf.getvalue()


# ----------------------------------------------------------------------------- parsing + validation
def parse_upload(filename: str, data: bytes) -> tuple[dict, list[str]]:
    """Return ({name, subject, aliases, questions:[validated dicts]}, errors)."""
    text = data.decode("utf-8-sig", errors="replace")
    errors: list[str] = []
    meta: dict = {}
    if filename.lower().endswith(".json") or text.lstrip().startswith(("{", "[")):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            return {}, [f"Not valid JSON: {exc}"]
        if isinstance(raw, list):
            items = raw
        else:
            items = raw.get("questions", [])
            meta = {k: raw.get(k) for k in ("name", "subject", "description", "aliases") if raw.get(k)}
    else:
        items = parse_csv(text)
    questions = []
    for i, item in enumerate(items, start=1):
        if item.get("_error"):
            errors.append(item["_error"])
            continue
        where = f"row {item.get('_row')}" if item.get("_row") else f"question {i}"
        item = {k: v for k, v in item.items() if not k.startswith("_")}
        try:
            questions.append(Question.model_validate(item).model_dump())
        except ValidationError as exc:
            first = exc.errors()[0]
            errors.append(f"{where}: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
    return {**meta, "questions": questions}, errors


def assign_codes(subject: str, questions: list[dict], existing: set[str] | None = None) -> None:
    prefix = re.sub(r"[^A-Z]", "", "".join(w[0] for w in subject.upper().split()))[:5] or "Q"
    taken = set(existing or set()) | {q["code"] for q in questions if q.get("code")}
    counters: dict[str, int] = {}
    for q in questions:
        if q.get("code"):
            continue
        p = {"warmup": "W", "fundamentals": "F", "depth": "D", "applied": "A"}[q["phase"]]
        while True:
            counters[p] = counters.get(p, 0) + 1
            code = f"{prefix}-{p}-{counters[p]:02d}"
            if code not in taken:
                break
        q["code"] = code
        taken.add(code)


def hint_leaks(hint: str, question: str, key_points: list[dict], aliases: dict | None = None) -> list[str]:
    """Key-point terms that appear in a hint but not in the question itself: the hint would give the answer away."""
    h = tokens(normalize(hint, aliases))
    qtok = tokens(normalize(question, aliases))
    leaks = []
    for kp in key_points:
        for term in kp.get("terms", []):
            tt = term_tokens(term, aliases)
            if len(" ".join(tt)) < 4:
                continue
            if phrase_in(tt, h, max_gap=1) and not phrase_in(tt, qtok, max_gap=1):
                leaks.append(term)
    return leaks


def validate_bank(questions: list[dict], aliases: dict | None = None) -> dict:
    """Coverage report + warnings shown to faculty before a bank can be used in a viva."""
    approved = [q for q in questions if q.get("status", "approved") == "approved"]
    by_phase = {p: sum(1 for q in approved if q["phase"] == p) for p in PHASES}
    topics: dict[str, dict] = {}
    for q in approved:
        t = topics.setdefault(q["topic"], {"fundamentals": 0, "depth": 0, "applied": 0, "warmup": 0})
        t[q["phase"]] += 1
    warnings, errors = [], []
    codes = [q["code"] for q in questions]
    for c in {c for c in codes if codes.count(c) > 1}:
        errors.append(f"Duplicate question code {c}")
    for q in approved:
        text = q.get("question") or q.get("text", "")
        if not q.get("key_points"):
            warnings.append(f"{q['code']}: no key points, so live follow-ups fall back to generic probes")
        if q["phase"] in ("fundamentals", "depth") and not q.get("follow_ups"):
            warnings.append(f"{q['code']}: no follow-ups; the examiner will use a generic 'why' probe")
        if not q.get("hints") and q["phase"] != "warmup":
            warnings.append(f"{q['code']}: no hint, so a partial answer moves straight on")
        for h in q.get("hints", []):
            leaks = hint_leaks(h, text, q.get("key_points", []), aliases)
            if leaks:
                warnings.append(f"{q['code']}: hint may give away the answer (mentions {', '.join(sorted(set(leaks))[:3])})")
        if not q.get("rephrase") and q["phase"] != "warmup":
            warnings.append(f"{q['code']}: no rephrased version; 'please explain the question' will repeat it verbatim")
    if by_phase["fundamentals"] < 3:
        errors.append(f"Needs at least 3 approved fundamentals questions (has {by_phase['fundamentals']})")
    if by_phase["depth"] < 1:
        errors.append("Needs at least 1 approved depth question")
    if by_phase["applied"] < 1:
        errors.append("Needs at least 1 approved applied problem")
    variety = by_phase["fundamentals"] >= 9 and by_phase["depth"] >= 4 and by_phase["applied"] >= 3
    if not variety and not errors:
        warnings.append("Small bank: students will often get the same questions. Aim for 9+ fundamentals, "
                        "4+ depth and 3+ applied questions so each student gets a different set.")
    return {"ready": not errors, "errors": errors, "warnings": warnings, "by_phase": by_phase, "topics": topics,
            "approved": len(approved), "total": len(questions), "vocabulary": len(bank_vocabulary(approved, aliases))}


def bank_vocabulary(questions: list[dict], aliases: dict | None = None) -> set[str]:
    """Words that signal an answer is on-topic: everything in questions, key points and follow-ups."""
    vocab: set[str] = set()
    for q in questions:
        parts = [q.get("question") or q.get("text", ""), q.get("rephrase", ""), q["topic"]]
        for kp in q.get("key_points", []):
            parts += [kp["point"], *kp.get("terms", [])]
        for f in q.get("follow_ups", []):
            parts.append(f["text"])
            for kp in f.get("key_points", []):
                parts += [kp["point"], *kp.get("terms", [])]
        for p in parts:
            vocab.update(content_tokens(p, aliases))
    return vocab


def row_to_question(row: dict) -> dict:
    """DB row -> the dict shape the engine uses."""
    return {
        "id": row["id"], "code": row["code"], "topic": row["topic"], "phase": row["phase"], "type": row["qtype"],
        "difficulty": row["difficulty"], "question": row["text"], "rephrase": row.get("rephrase") or "",
        "key_points": row.get("key_points") or [], "follow_ups": row.get("follow_ups") or [], "hints": row.get("hints") or [],
        "misconceptions": row.get("misconceptions") or [], "code_snippet": row.get("code_snippet") or "",
        "expected_seconds": row.get("expected_s") or 60, "tags": row.get("tags") or [], "status": row["status"],
        "source": row.get("source"),
    }
