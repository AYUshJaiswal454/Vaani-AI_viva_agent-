"""Run audio bots against a running Vaani server: sample vivas, robustness runs and load tests.

    # five full vivas at once, one persona each (the sample recordings)
    python loadtest/run_bots.py --code DBMS26 --personas strong,average,weak,idk,indian_english --label samples

    # robustness: network drop at 2 min, noisy room, muted microphone
    python loadtest/run_bots.py --code DBMS26 --personas strong --drop-at 120 --label drop
    python loadtest/run_bots.py --code DBMS26 --personas average --snr 10 --label noise

    # load: N bots (personas cycle), real-time audio, stub speech recognition on the server for repeatable numbers
    python loadtest/run_bots.py --code LOAD01 --bots 150 --ramp 60 --max-s 240 --hints --label load150

Results (per-bot latencies, errors, durations) go to loadtest/results/<time>-<label>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loadtest.bot import AudioBot, httpx  # noqa: E402
from tests.personas import PERSONAS  # noqa: E402

VOICES = ["awb", "rms", "kal16", "kal", "slt"]
# Fictional names for the sample recordings (small runs); big load runs use "<persona> Bot N".
NAMES = ["Ananya Krishnan", "Rohan Gupta", "Sneha Patil", "Vikram Rao", "Farhan Qureshi", "Divya Menon", "Karan Sethi",
         "Pooja Deshmukh", "Aditya Kulkarni", "Nisha Bansal"]


def pct(xs: list[float], p: float):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))]) if xs else None


async def main_async(args) -> dict:
    bank = json.loads((ROOT / "app" / "data" / "banks" / f"{args.bank}.json").read_text(encoding="utf-8"))
    personas = args.personas.split(",")
    n = args.bots or len(personas)
    limits = httpx.Limits(max_connections=max(50, n * 2), max_keepalive_connections=max(20, n))
    async with httpx.AsyncClient(timeout=60, limits=limits) as http:
        bots = []
        for i in range(n):
            p = personas[i % len(personas)]
            bots.append(AudioBot(args.base, args.code, f"{args.roll_prefix}{i + 1:03d}", PERSONAS[p](seed=args.seed + i),
                                 name=NAMES[i] if n <= len(NAMES) else f"{p.replace('_', ' ').title()} Bot {i + 1}",
                                 voice=VOICES[i % len(VOICES)],
                                 snr_db=args.snr, mute_after_s=args.mute_after, drop_at_s=args.drop_at, drop_for_s=args.drop_for,
                                 use_hints=args.hints, bank=bank, max_s=args.max_s, fetch_audio=not args.no_fetch, seed=args.seed + i))
        tasks = []
        t0 = time.monotonic()
        for b in bots:
            tasks.append(asyncio.create_task(b.run(http)))
            await asyncio.sleep(args.ramp / max(1, n))
        results = await asyncio.gather(*tasks)
        wall = time.monotonic() - t0
    lat = [x for r in results for x in r.latencies_ms]
    fetch = [x for r in results for x in r.audio_fetch_ms]
    out = {
        "label": args.label, "base": args.base, "bots": n, "personas": personas, "wall_s": round(wall, 1),
        "completed": sum(1 for r in results if r.ok), "still_running_at_end": sum(1 for r in results if not r.ok and not r.error),
        "dropped": sum(1 for r in results if r.error), "errors": [r.error for r in results if r.error][:20], "replies": len(lat),
        "reply_p50_ms": pct(lat, 0.5), "reply_p95_ms": pct(lat, 0.95), "reply_p99_ms": pct(lat, 0.99),
        "reply_max_ms": round(max(lat)) if lat else None, "audio_fetch_p95_ms": pct(fetch, 0.95),
        "options": {k: v for k, v in vars(args).items() if k not in ("base",)},
        "sessions": [{"roll_no": r.roll_no, "persona": r.persona, "session_id": r.session_id, "ok": r.ok, "end": r.end_reason,
                      "error": r.error, "turns": r.turns, "duration_s": r.duration_s, "reconnects": r.reconnects,
                      "reply_p50_ms": pct(r.latencies_ms, 0.5), "reply_max_ms": round(max(r.latencies_ms)) if r.latencies_ms else None,
                      "says": r.says if n <= 10 else []} for r in results],
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--code", required=True)
    ap.add_argument("--bank", default="dbms")
    ap.add_argument("--personas", default="strong,average,weak,idk,indian_english")
    ap.add_argument("--bots", type=int, default=0)
    ap.add_argument("--ramp", type=float, default=2.0)
    ap.add_argument("--max-s", type=float, default=960)
    ap.add_argument("--snr", type=float, default=None)
    ap.add_argument("--mute-after", type=float, default=None)
    ap.add_argument("--drop-at", type=float, default=None)
    ap.add_argument("--drop-for", type=float, default=8.0)
    ap.add_argument("--hints", action="store_true", help="tell the server's stub recogniser the words (load tests)")
    ap.add_argument("--no-fetch", action="store_true", help="don't download examiner audio")
    ap.add_argument("--roll-prefix", default="BOT")
    ap.add_argument("--seed", type=int, default=random.randint(1, 10_000))
    ap.add_argument("--label", default="run")
    args = ap.parse_args()
    res = asyncio.run(main_async(args))
    out_dir = ROOT / "loadtest" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.label}.json"
    path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    summary = {k: v for k, v in res.items() if k not in ("sessions", "options")}
    print(json.dumps(summary, indent=1))
    print("results:", path)


if __name__ == "__main__":
    main()
