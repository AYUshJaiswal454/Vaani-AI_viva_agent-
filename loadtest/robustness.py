"""Robustness scenarios, run at the same time against a live server, each a real viva by an audio bot:

    drop     the network drops for 10 s mid-answer, the client reconnects and resends what it captured
    noise    a noisy room (fan hum and clatter at 10 dB SNR)
    mute     the microphone goes silent (muted) after a minute
    silence  a student who stays silent on some questions (the silence ladder)

For each, the script also reads the server's record as faculty (end reason, integrity signals, transcript size)
to check what the system did, and writes loadtest/results/<time>-robustness.json.

    python loadtest/robustness.py --code DBMS26 --duration 300
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loadtest.bot import H, AudioBot, httpx  # noqa: E402
from tests.personas import PERSONAS  # noqa: E402

SCENARIOS = {
    "drop": dict(persona="strong", drop_at_s=95, drop_for_s=10),
    "noise": dict(persona="average", snr_db=10),
    "mute": dict(persona="strong", mute_after_s=60),
    "silence": dict(persona="silent"),
}


async def main_async(args) -> dict:
    bank = json.loads((ROOT / "app" / "data" / "banks" / "dbms.json").read_text(encoding="utf-8"))
    names = [s for s in args.scenarios.split(",") if s in SCENARIOS]
    async with httpx.AsyncClient(timeout=60) as http:
        bots = {}
        for i, name in enumerate(names):
            cfg = dict(SCENARIOS[name])
            persona = PERSONAS[cfg.pop("persona")](seed=60 + i)
            bots[name] = AudioBot(args.base, args.code, f"{args.roll_prefix}{i + 1:02d}", persona, name=f"Robust {name.title()}",
                                  voice=["awb", "rms", "slt", "kal16"][i % 4], bank=bank, max_s=args.duration, seed=60 + i, **cfg)
        results = await asyncio.gather(*(b.run(http) for b in bots.values()))
        r = await http.post(args.base + "/api/auth/login", json={"email": args.faculty_email, "password": args.faculty_password}, headers=H)
        auth = {**H, "authorization": f"Bearer {r.json()['token']}"}
        await asyncio.sleep(3)
        out = {}
        for name, res in zip(bots, results):
            detail = {}
            if res.session_id:
                d = (await http.get(f"{args.base}/api/sessions/{res.session_id}", headers=auth)).json()
                integ = d.get("integrity") or []
                detail = {"status": d["session"]["status"], "end_reason": d["session"]["end_reason"], "paused_s": d["session"]["paused_s"],
                          "integrity": sorted({e["kind"] for e in integ}) if isinstance(integ, list) else integ,
                          "student_turns": sum(1 for t in d["turns"] if t["speaker"] == "student"),
                          "examiner_kinds": sorted({t["kind"] for t in d["turns"] if t["speaker"] == "examiner"})}
            lat = sorted(res.latencies_ms)
            out[name] = {"ok": res.ok, "error": res.error, "end": res.end_reason, "turns": res.turns, "reconnects": res.reconnects,
                         "duration_s": res.duration_s, "reply_p50_ms": round(lat[len(lat) // 2]) if lat else None,
                         "reply_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))]) if lat else None,
                         "session_id": res.session_id, "server": detail,
                         "says": [s for s in res.says if s["kind"] in ("prompt", "rephrase", "redirect", "repeat")][:12]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--code", required=True)
    ap.add_argument("--scenarios", default="drop,noise,mute,silence")
    ap.add_argument("--duration", type=float, default=300)
    ap.add_argument("--roll-prefix", default="23RB")
    ap.add_argument("--faculty-email", default="faculty@vaani.local")
    ap.add_argument("--faculty-password", default="faculty123")
    args = ap.parse_args()
    out = asyncio.run(main_async(args))
    path = ROOT / "loadtest" / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-robustness.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    for k, v in out.items():
        print(f"{k:8s} ok={v['ok']} end={v['end']!r} reconnects={v['reconnects']} p50={v['reply_p50_ms']} p95={v['reply_p95_ms']} "
              f"server={json.dumps(v['server'])}")
    print("results:", path)


if __name__ == "__main__":
    main()
