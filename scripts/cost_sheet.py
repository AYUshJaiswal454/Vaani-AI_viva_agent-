"""Cost sheet: what it costs to run a 15-minute viva for 350 students, from measured usage.

Usage per viva is measured from the recorded sample vivas (samples/vivas/*/session.json): how long students speak
(speech-to-text minutes), how many characters the examiner says and how many of those are unique to the student
(text-to-speech; the rest is cached per question bank), and the size of the scoring prompt built from the real
transcripts (LLM tokens). Capacity (vivas per server) comes from the load tests (docs/data/capacity.json).
Prices are in app/data/pricing.json (sources and dates inside) plus the server prices below.

    python scripts/cost_sheet.py            # writes docs/data/cost.json and docs/COST.md
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# On-demand Linux prices, AWS Mumbai (ap-south-1), from doit.com/compute (checked 2026-10-21).
SERVERS = {
    "c7i.large": {"vcpu": 2, "ram_gb": 4, "usd_h": 0.0892},
    "c7i.xlarge": {"vcpu": 4, "ram_gb": 8, "usd_h": 0.1785},
    "c7i.2xlarge": {"vcpu": 8, "ram_gb": 16, "usd_h": 0.3570},
}
STUDENTS = 350
SLOT_MIN = 18            # 15-minute viva + joining, consent and mic check
CHARS_PER_TOKEN = 3.8    # English prose with code-like terms; conservative
SCORER_OUT_TOKENS = 900  # JSON: five dimensions with reasons and evidence ids, 3-5 feedback lines (max_tokens 1800)
LIVE_IN_TOKENS, LIVE_OUT_TOKENS = 420, 40   # one follow-up rephrasing call (system + recent transcript), when enabled
EXAMINER_CHARS_PER_S = 14.5                 # speaking rate used to turn characters into TTS minutes


def capacity_from_runs(target_ms: float = 2500) -> dict:
    """Concurrent vivas per server from the load-test result files named in docs/data/runs.json."""
    runs = json.loads((ROOT / "docs" / "data" / "runs.json").read_text())
    res = ROOT / "loadtest" / "results"
    cap = {"source": {k: runs.get(k) for k in ("stt_capacity", "load")}}
    if runs.get("stt_capacity"):
        r = json.loads((res / runs["stt_capacity"]).read_text())
        cores = r.get("cores") or 2
        cap["whisper_base_concurrent"] = r["capacity"]
        cap["whisper_test_vcpu"] = cores
        cap["whisper_base_concurrent_per_vcpu"] = round(r["capacity"] / cores, 2)
    if runs.get("load"):
        r = json.loads((res / runs["load"]).read_text())
        ok = [st["bots"] for st in r["stages"] if not st["dropped"] and st["reply_p95_ms"] is not None and st["reply_p95_ms"] <= target_ms]
        cap["stub_concurrent_ok"] = max(ok) if ok else 0
        cap["stub_test_vcpu"] = r.get("cores")
    cap.setdefault("whisper_base_concurrent_per_vcpu", 1.0)
    cap.setdefault("stub_concurrent_ok", 100)
    (ROOT / "docs" / "data" / "capacity.json").write_text(json.dumps(cap, indent=1), encoding="utf-8")
    return cap


def measure() -> dict:
    os.environ.setdefault("SEED_DEMO", "false")
    from app.scoring.llm_scorer import build_messages
    from app.scoring.rubric import default_rubric

    rubric = default_rubric()
    bank = {q["code"]: q for q in json.loads((ROOT / "app" / "data" / "banks" / "dbms.json").read_text())["questions"]}
    rows = []
    for f in sorted((ROOT / "samples" / "vivas").glob("*/session.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        turns = doc["turns"]
        stu = [t for t in turns if t["speaker"] == "student"]
        exa = [t for t in turns if t["speaker"] == "examiner"]
        # lines with the student's name or LLM-reworded follow-ups are synthesised per student; the rest is cached
        unique = [t for t in exa if any(x in (t.get("source") or "") for x in ("variant:", "system:greeting"))]
        msgs = build_messages(turns, {**bank, **(doc["session"]["plan"].get("snapshot") or {})}, rubric, "Database Management Systems")
        prompt_chars = sum(len(m["content"]) for m in msgs)
        folder = f.parent
        audio_bytes = sum((folder / n).stat().st_size for n in ("conversation.ogg", "student.ogg") if (folder / n).exists())
        rows.append({
            "sample": folder.name,
            "duration_s": max((t.get("t_end") or t["t_start"]) for t in turns),
            "student_speech_s": sum(max(0.0, (t.get("t_end") or t["t_start"]) - t["t_start"]) for t in stu),
            "answers": len(stu),
            "followups": sum(1 for t in exa if t["kind"] == "followup"),
            "examiner_chars": sum(len(t["text"]) for t in exa),
            "unique_examiner_chars": sum(len(t["text"]) for t in unique),
            "scorer_prompt_tokens": round(prompt_chars / CHARS_PER_TOKEN),
            "audio_mb": round(audio_bytes / 1e6, 2),
        })
    if not rows:
        raise SystemExit("no samples in samples/vivas: run the sample vivas and scripts/export_samples.py first")
    avg = {k: round(statistics.mean(r[k] for r in rows), 2) for k in rows[0] if k != "sample"}
    return {"per_sample": rows, "average": avg}


def bank_chars() -> int:
    """Characters of every phrase in the demo bank (questions, rephrasings, follow-ups, hints): synthesised once."""
    bank = json.loads((ROOT / "app" / "data" / "banks" / "dbms.json").read_text())["questions"]
    n = 0
    for q in bank:
        n += len(q.get("question", "")) + len(q.get("rephrase", "")) + sum(len(h) for h in q.get("hints", []))
        n += sum(len(f["text"]) for f in q.get("follow_ups", []))
    return n + 4000      # + fixed system lines (rules, prompts, acknowledgements)


def scenarios(m: dict, cap: dict) -> list[dict]:
    p = json.loads((ROOT / "app" / "data" / "pricing.json").read_text())
    inr = p["usd_to_inr"]
    a = m["average"]
    stt_min = a["student_speech_s"] / 60
    tts_unique_min = a["unique_examiner_chars"] / EXAMINER_CHARS_PER_S / 60
    tts_bank_min = bank_chars() / EXAMINER_CHARS_PER_S / 60
    scorer_in = a["scorer_prompt_tokens"]

    def llm(model: str, tin: float, tout: float) -> float:
        pr = p["models"][model]
        return (tin * pr["input"] + tout * pr["output"]) / 1e6

    def server(kind: str, concurrent: int) -> dict:
        srv = SERVERS[kind]
        slots = math.ceil(STUDENTS / max(1, concurrent))
        hours = slots * SLOT_MIN / 60
        return {"server": kind, "concurrent_vivas": concurrent, "rounds": slots, "hours": round(hours, 1),
                "usd": round(hours * srv["usd_h"], 2)}

    local_conc = cap.get("whisper_base_concurrent_per_vcpu", 1.0)
    out = []

    # 1. Everything on the college's own server: local Whisper, local voice, rule scorer (+ optional local LLM).
    s1 = server("c7i.2xlarge", int(local_conc * SERVERS["c7i.2xlarge"]["vcpu"]))
    out.append({"name": "Campus server, no paid APIs", "stt": "Whisper base.en on the server", "tts": "Piper / flite on the server (or the browser)",
                "scoring": "rule scorer + faculty review (optional local LLM via Ollama)", "per_viva_usd": {"stt": 0, "tts": 0, "llm": 0},
                "compute": s1, "notes": "Audio never leaves the campus. Throughput limited by CPU speech recognition."})

    hosted = server("c7i.large", min(cap.get("stub_concurrent_ok", 100), STUDENTS))
    # 2. Cheapest hosted: Groq Whisper + browser voice + open-weight models on Groq.
    stt2 = stt_min * p["stt_per_minute_usd"]["whisper-large-v3-turbo"]
    llm2 = 3 * llm("openai/gpt-oss-120b", scorer_in, SCORER_OUT_TOKENS) + a["followups"] * llm("openai/gpt-oss-20b", LIVE_IN_TOKENS, LIVE_OUT_TOKENS)
    out.append({"name": "Hosted APIs, lowest cost", "stt": "Groq whisper-large-v3-turbo", "tts": "browser voice (free)",
                "scoring": "gpt-oss-120b x3 runs (median) on Groq; gpt-oss-20b for live follow-up wording",
                "per_viva_usd": {"stt": stt2, "tts": 0.0, "llm": llm2}, "compute": hosted,
                "notes": "Best price for quality. Answer audio and transcripts go to Groq's servers."})

    # 3. Premium: OpenAI end to end, natural examiner voice.
    stt3 = stt_min * p["stt_per_minute_usd"]["gpt-4o-mini-transcribe"]
    tts3 = tts_unique_min * p["tts_per_minute_usd"]["gpt-4o-mini-tts"]
    llm3 = 3 * llm("gpt-6.1-sol", scorer_in, SCORER_OUT_TOKENS) + a["followups"] * llm("gpt-6-luna", LIVE_IN_TOKENS, LIVE_OUT_TOKENS)
    out.append({"name": "Hosted APIs, premium", "stt": "OpenAI gpt-4o-mini-transcribe", "tts": "OpenAI gpt-4o-mini-tts (bank phrases cached)",
                "scoring": "gpt-6.1-sol x3 runs; gpt-6-luna for live follow-ups",
                "per_viva_usd": {"stt": stt3, "tts": tts3, "llm": llm3}, "compute": hosted,
                "one_time_usd": {"tts_bank": round(tts_bank_min * p["tts_per_minute_usd"]["gpt-4o-mini-tts"], 2)},
                "notes": "Most natural voice and strongest scorer."})

    # 4. Indian languages: Sarvam for speech (Hinglish), open model for scoring.
    stt4 = a["student_speech_s"] / 3600 * p["sarvam_inr"]["stt_per_hour"] / inr
    tts4 = a["unique_examiner_chars"] / 10000 * p["sarvam_inr"]["tts_per_10k_chars"] / inr
    out.append({"name": "Indian-language speech", "stt": "Sarvam saaras:v4 (Hinglish, Indian English)", "tts": "Sarvam bulbul:v2",
                "scoring": "gpt-oss-120b x3 runs on Groq", "per_viva_usd": {"stt": stt4, "tts": tts4, "llm": 3 * llm("openai/gpt-oss-120b", scorer_in, SCORER_OUT_TOKENS)},
                "compute": hosted, "one_time_usd": {"tts_bank": round(bank_chars() / 10000 * p["sarvam_inr"]["tts_per_10k_chars"] / inr, 2)},
                "notes": "An Indian provider built for Indian languages; the best option for Hinglish answers."})

    for s in out:
        pv = s["per_viva_usd"]
        s["per_viva_usd"] = {k: round(v, 5) for k, v in pv.items()}
        api = sum(pv.values()) * STUDENTS
        one = sum((s.get("one_time_usd") or {}).values())
        total = api + one + s["compute"]["usd"]
        s["total_350_usd"] = round(total, 2)
        s["total_350_inr"] = round(total * inr)
        s["per_student_inr"] = round(total * inr / STUDENTS, 2)
    return out


def write_md(m: dict, cap: dict, sc: list[dict], path: Path) -> None:
    a = m["average"]
    lines = ["# Cost sheet: 350 students, one 15-minute viva each", "",
             f"Generated {time.strftime('%Y-%m-%d')} by `scripts/cost_sheet.py` from the {len(m['per_sample'])} recorded sample vivas "
             "and the load-test results. Prices: `app/data/pricing.json` (sources and dates inside); servers: AWS Mumbai on-demand.", "",
             "## Measured usage per viva", "",
             "| Quantity | Average | Used for |", "|---|---|---|",
             f"| Viva length | {a['duration_s'] / 60:.1f} min | server time |",
             f"| Student speech | {a['student_speech_s'] / 60:.1f} min | speech-to-text minutes |",
             f"| Answers / follow-ups | {a['answers']:.0f} / {a['followups']:.0f} | live LLM calls (only if follow-up rewording is on) |",
             f"| Examiner text | {a['examiner_chars']:.0f} chars, {a['unique_examiner_chars']:.0f} unique to the student | text-to-speech (bank phrases are cached per viva) |",
             f"| Scoring prompt | {a['scorer_prompt_tokens']:.0f} tokens in, ~{SCORER_OUT_TOKENS} out, x3 runs | scorer LLM |",
             f"| Stored audio | {a['audio_mb']:.1f} MB | storage ({a['audio_mb'] * STUDENTS / 1000:.1f} GB for 350 students) |", "",
             "## Four ways to run it", "",
             "| Setup | Speech-to-text | Voice | Scoring | API cost / viva | Server | Total for 350 | Per student |",
             "|---|---|---|---|---|---|---|---|"]
    for s in sc:
        api = sum(s["per_viva_usd"].values())
        c = s["compute"]
        lines.append(f"| **{s['name']}** | {s['stt']} | {s['tts']} | {s['scoring']} | ${api:.4f} | {c['server']} x {c['hours']} h "
                     f"({c['concurrent_vivas']} at a time) | **${s['total_350_usd']:.2f} (₹{s['total_350_inr']:,})** | ₹{s['per_student_inr']:.2f} |")
    lines += ["", "Notes:", ""] + [f"- **{s['name']}**: {s['notes']}" for s in sc]
    lines += ["", "## How the numbers are built", "",
              f"- Server time: {STUDENTS} students in rounds of N concurrent vivas, {SLOT_MIN} minutes per round (viva + joining). "
              f"N comes from the load tests: {cap.get('stub_concurrent_ok')} concurrent vivas on 2 vCPU with hosted speech recognition, "
              f"{cap.get('whisper_base_concurrent_per_vcpu')} per vCPU with local Whisper base.en (see docs/SCALABILITY.md).",
              f"- Speech-to-text is billed on student speech only (the examiner's voice is never transcribed).",
              f"- LLM tokens: the real scoring prompt (rubric + reference points + transcript) built from each sample, "
              f"{CHARS_PER_TOKEN} characters per token, {SCORER_OUT_TOKENS} output tokens per run (an estimate; the cap is 1,800).",
              "- Not included: faculty time, the college's network, and a database server (SQLite on the same machine is enough "
              "for 350 students; Postgres is supported for multi-server setups).", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    cap = capacity_from_runs()
    m = measure()
    sc = scenarios(m, cap)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "students": STUDENTS, "usage": m, "capacity": cap, "servers": SERVERS, "scenarios": sc}
    (ROOT / "docs" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "data" / "cost.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    write_md(m, cap, sc, ROOT / "docs" / "COST.md")
    for s in sc:
        print(f"{s['name']:32s} ${s['total_350_usd']:8.2f}  ₹{s['total_350_inr']:>8,}  ₹{s['per_student_inr']}/student")


if __name__ == "__main__":
    main()
