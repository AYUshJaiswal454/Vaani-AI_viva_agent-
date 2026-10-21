"""The scored record of a viva: transcript lines with ids and timestamps, grouped by question."""
from __future__ import annotations

from dataclasses import dataclass, field

SUBSTANTIVE = {"strong", "partial", "weak", "idk", "offtopic"}


def mmss(t: float | None) -> str:
    t = max(0.0, float(t or 0))
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def line_id(seq: int) -> str:
    return f"L{seq:02d}"


@dataclass
class Item:
    code: str
    question: dict
    phase: str
    asked_seq: int
    asked_t: float
    main: list[dict] = field(default_factory=list)       # student answer turns to the main question (incl. after hint)
    followups: list[dict] = field(default_factory=list)  # student answer turns to follow-ups
    n_followups_asked: int = 0
    hint_given: bool = False


def answer_of(turn: dict) -> dict:
    return (turn.get("meta") or {}).get("answer") or {}


def group_items(turns: list[dict], questions: dict[str, dict]) -> list[Item]:
    """Walk the transcript in order and attach each student answer to the question it answered."""
    items: dict[str, Item] = {}
    order: list[str] = []
    for t in turns:
        code = t.get("question_code")
        if t["speaker"] == "examiner" and code and code in questions:
            if code not in items:
                if t["kind"] in ("question",):
                    items[code] = Item(code=code, question=questions[code], phase=t.get("phase") or questions[code]["phase"],
                                       asked_seq=t["seq"], asked_t=t["t_start"])
                    order.append(code)
                continue
            if t["kind"] == "followup":
                items[code].n_followups_asked += 1
            if t["kind"] == "hint":
                items[code].hint_given = True
        elif t["speaker"] == "student":
            a = answer_of(t)
            code = a.get("code")
            if not code or code not in items:
                continue
            if a.get("kind") == "followup":
                items[code].followups.append(t)
            else:
                items[code].main.append(t)
    return [items[c] for c in order]


def _examiner_content(t: dict) -> str:
    """Only what was asked (bank questions, follow-ups, hints, logged rewordings), without acknowledgements, the rules
    or other fixed system lines: keeps the scorer prompt small enough for free-plan rate limits."""
    segs = (t.get("meta") or {}).get("segments") or []
    asked = [s["text"] for s in segs if str(s.get("source", "")).startswith(("bank:", "variant:"))]
    if asked:
        return " ".join(asked)
    return f"({t['kind']})" if segs else t["text"]


def transcript_text(turns: list[dict], include_examiner_meta: bool = True, compact: bool = False) -> str:
    """Numbered transcript for the scorer prompt and for exports. `compact` drops fixed examiner phrasing."""
    out = []
    for t in turns:
        if t["speaker"] == "system":
            continue
        span = f"{mmss(t['t_start'])}" + (f"-{mmss(t['t_end'])}" if t.get("t_end") and t["speaker"] == "student" else "")
        if t["speaker"] == "examiner":
            tag = t["kind"]
            if include_examiner_meta and t.get("question_code"):
                tag += f" {t['question_code']}"
            if t.get("phase"):
                tag += f", {t['phase']}"
            out.append(f"{line_id(t['seq'])} [{span}] EXAMINER ({tag}): {_examiner_content(t) if compact else t['text']}")
        else:
            extra = ""
            a = answer_of(t)
            if a.get("cut"):
                extra = " [cut off at the 90-second limit]"
            out.append(f"{line_id(t['seq'])} [{span}] STUDENT{extra}: {t['text']}")
    return "\n".join(out)
