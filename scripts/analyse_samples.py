"""Check the examiner's behaviour in the recorded sample vivas against the brief's rules -> docs/data/behaviour.json.

    python scripts/analyse_samples.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine.text import EVALUATIVE  # noqa: E402


def main() -> None:
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "samples" / "vivas").glob("*/session.json"))]
    if not docs:
        raise SystemExit("no samples")
    ex_lines = traced = 0
    evaluative = []
    strong = strong_deeper = partial = partial_hint = double_hint = idk = idk_moved = 0
    durations, phase_starts = [], {"depth": [], "applied": [], "wrapup": []}
    bank_questions = []
    for d in docs:
        turns = d["turns"]
        st = d["session"]["state"] or {}
        durations.append(max((t.get("t_end") or t["t_start"]) for t in turns))
        for p in st.get("phase_log", []):
            if p["phase"] in phase_starts:
                phase_starts[p["phase"]].append(p["t"])
        bank_questions.append(len({t["question_code"] for t in turns if t["speaker"] == "examiner" and t.get("question_code")}))
        hints_per_q: dict[str, int] = {}
        for i, t in enumerate(turns):
            if t["speaker"] == "examiner":
                ex_lines += 1
                src = t.get("source") or ""
                if all(re.match(r"^(bank:|system:|variant:)", part) for part in src.split(",") if part):
                    traced += 1 if src else 0
                for seg in (t.get("meta") or {}).get("segments") or []:
                    if not seg.get("source", "").startswith("bank:") and seg.get("source") != "system:greeting" and EVALUATIVE.search(seg.get("text", "")):
                        evaluative.append(seg["text"])
                if t["kind"] == "hint" and t.get("question_code"):
                    hints_per_q[t["question_code"]] = hints_per_q.get(t["question_code"], 0) + 1
                continue
            a = (t.get("meta") or {}).get("answer") or {}
            nxt = next((x for x in turns[i + 1:] if x["speaker"] == "examiner"), None)
            if not nxt or a.get("phase") in (None, "warmup", "wrapup"):
                continue
            label = a.get("label")
            if label == "strong" and a.get("kind") == "question":
                strong += 1
                strong_deeper += nxt["kind"] == "followup"
            if label == "partial" and a.get("kind") == "question" and not a.get("after_hint"):
                partial += 1
                partial_hint += nxt["kind"] == "hint"
            if label == "idk":
                idk += 1
                idk_moved += nxt["kind"] in ("question", "wrapup", "close")
        double_hint += sum(1 for v in hints_per_q.values() if v > 1)

    def avg(xs):
        return f"{statistics.mean(xs) / 60:.1f} min" if xs else "–"

    out = {
        "Every examiner line traceable to the bank or a named system line": f"{traced} of {ex_lines}",
        "Evaluative words (\"correct\", \"exactly\"...) in non-bank lines": str(len(evaluative)),
        "Distinct bank questions per viva": ", ".join(str(n) for n in bank_questions),
        "Longest viva (hard stop 15:00)": f"{max(durations) / 60:.1f} min",
        "Average start of depth / applied / wrap-up": f"{avg(phase_starts['depth'])} / {avg(phase_starts['applied'])} / {avg(phase_starts['wrapup'])}",
        "Strong answer to a main question -> deeper follow-up": f"{strong_deeper} of {strong}" + (" (the rest: no follow-up left or not enough time in the phase)" if strong_deeper < strong else ""),
        "Partial answer -> one hint": f"{partial_hint} of {partial}" + (" (the rest: no hint in the bank or time short)" if partial_hint < partial else ""),
        "Questions that got a second hint": str(double_hint),
        "\"I don't know\" -> moved on (no hint, no pressure)": f"{idk_moved} of {idk}",
    }
    (ROOT / "docs" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "data" / "behaviour.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if evaluative:
        print("evaluative lines:", evaluative[:5])


if __name__ == "__main__":
    main()
