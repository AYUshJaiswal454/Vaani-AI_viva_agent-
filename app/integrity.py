"""Integrity signals computed after the viva. They are signals for faculty, never automatic penalties.

* reading_suspected: a long pause after the question, then a long answer delivered unusually fast and fluently
  (no fillers), which is what reading from another screen sounds like. Two or more such answers raise a warning.
* second_voice: the pitch of an answer is far from the student's own typical pitch (estimated from their first
  answers). This catches someone else answering, but also a cold or a shout, so it is only a prompt to listen.
* tab switches, pasting into the scratchpad, mic muted and network drops are recorded live by the browser/server.
"""
from __future__ import annotations

from statistics import median

import numpy as np

from .db import get_db, now_iso
from .engine.text import filler_ratio
from .scoring.transcript import answer_of
from .voice.audio import SR, pcm_to_float

READ_PAUSE_S = 6.0
READ_WORDS = 40
READ_RATE = 2.7          # words per second (~160 wpm) sustained over a long answer


def pitch_hz(x: np.ndarray) -> float | None:
    """Median F0 of voiced 40 ms frames by autocorrelation (80-400 Hz)."""
    frame = int(0.04 * SR)
    f0s = []
    for i in range(0, len(x) - frame, frame):
        f = x[i:i + frame]
        if np.sqrt(np.mean(f ** 2)) < 0.02:
            continue
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[frame - 1:]
        lo, hi = SR // 400, SR // 80
        if hi >= len(ac):
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[0] > 0 and ac[k] / ac[0] > 0.45:
            f0s.append(SR / k)
    return float(median(f0s)) if len(f0s) >= 10 else None


def analyse(session_id: int, student_pcm: bytes) -> list[dict]:
    db = get_db()
    turns = db.fetchall("SELECT * FROM turns WHERE session_id = ? ORDER BY seq", (session_id,))
    events: list[dict] = []
    prev_examiner_end = None
    readers = []
    pitches = []
    for t in turns:
        if t["speaker"] == "examiner":
            dur = sum(len(s.get("text", "")) for s in (t.get("meta") or {}).get("segments", [])) / 15.0
            prev_examiner_end = t["t_start"] + dur
            continue
        a = answer_of(t)
        if a.get("label") not in ("strong", "partial", "weak"):
            continue
        words = len(t["text"].split())
        dur = max(0.5, (t.get("t_end") or t["t_start"]) - t["t_start"])
        pause = t["t_start"] - prev_examiner_end if prev_examiner_end is not None else 0.0
        rate = words / dur
        if pause >= READ_PAUSE_S and words >= READ_WORDS and rate >= READ_RATE and filler_ratio(t["text"]) < 0.01:
            readers.append({"line": t["seq"], "pause_s": round(pause, 1), "words_per_s": round(rate, 2)})
        if student_pcm:
            seg = pcm_to_float(student_pcm[int(t["t_start"] * SR) * 2:int((t.get("t_end") or t["t_start"]) * SR) * 2])
            if seg.size > SR:
                p = pitch_hz(seg)
                if p:
                    pitches.append((t["seq"], t["t_start"], p))
    for r in readers:
        events.append({"kind": "reading_suspected", "severity": "warn" if len(readers) >= 2 else "info", "t": 0.0, "detail": r})
    if len(pitches) >= 4:
        base = median(p for _, _, p in pitches[:3])
        for seq, t0, p in pitches[3:]:
            if abs(p - base) / base > 0.45:
                events.append({"kind": "second_voice", "severity": "info", "t": t0,
                               "detail": {"line": seq, "pitch_hz": round(p), "usual_hz": round(base)}})
        sv = [e for e in events if e["kind"] == "second_voice"]
        if len(sv) >= 2:
            for e in sv:
                e["severity"] = "warn"
    for e in events:
        if e["kind"] == "reading_suspected":
            line = next((x for x in turns if x["seq"] == e["detail"]["line"]), None)
            e["t"] = line["t_start"] if line else 0.0
        db.insert("integrity_events", {"session_id": session_id, "t": round(e["t"], 1), "kind": e["kind"], "severity": e["severity"],
                                       "detail": e["detail"], "created_at": now_iso()})
    return events


def summary(session_id: int) -> dict:
    rows = get_db().fetchall("SELECT kind, severity, COUNT(*) AS n FROM integrity_events WHERE session_id = ? GROUP BY kind, severity",
                             (session_id,))
    out: dict = {"warn": 0, "info": 0, "kinds": {}}
    for r in rows:
        out[r["severity"]] = out.get(r["severity"], 0) + r["n"]
        out["kinds"][r["kind"]] = out["kinds"].get(r["kind"], 0) + r["n"]
    return out
