"""How many concurrent vivas can one server recognise locally with Whisper before replies get slow?

Runs audio bots in stages (default 2, 4, 6, 8 at once) against a server using STT_ENGINE=whisper-onnx, each stage
for `--duration` seconds of real-time conversation, and records reply latency and server CPU per stage. The
capacity is the largest stage whose reply p95 stays under the target (2.5 s).

    python loadtest/stt_capacity.py --code DBMS26 --pid <server pid> --stages 2,4,6,8 --duration 240
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

from loadtest import run_bots  # noqa: E402
from loadtest.load import Monitor  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--code", required=True)
    ap.add_argument("--stages", default="2,4,6,8")
    ap.add_argument("--duration", type=float, default=240)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--target-ms", type=float, default=2500)
    ap.add_argument("--pause", type=float, default=20)
    ap.add_argument("--label", default="stt-capacity")
    args = ap.parse_args()
    stages = []
    for i, n in enumerate(int(x) for x in args.stages.split(",")):
        print(f"=== {n} concurrent vivas with local recognition", flush=True)
        mon = Monitor(args.pid)
        mon.start()
        ns = argparse.Namespace(base=args.base, code=args.code, bank="dbms", personas="strong,average,indian_english,weak",
                                bots=n, ramp=min(20.0, 4.0 * n), max_s=args.duration, snr=None, mute_after=None, drop_at=None,
                                drop_for=8.0, hints=False, no_fetch=False, roll_prefix=f"C{n}S{i}X", seed=300 + i,
                                label=f"{args.label}-{n}")
        res = asyncio.run(run_bots.main_async(ns))
        mon.stop_flag = True
        mon.join(3)
        res["resources"] = mon.summary()
        res.pop("sessions", None)
        res["meets_target"] = bool(res["reply_p95_ms"] is not None and res["reply_p95_ms"] <= args.target_ms and not res["dropped"])
        stages.append(res)
        print(json.dumps({k: res[k] for k in ("bots", "replies", "reply_p50_ms", "reply_p95_ms", "reply_max_ms", "dropped",
                                               "meets_target", "resources")}, indent=1), flush=True)
        time.sleep(args.pause)
    ok = [s["bots"] for s in stages if s["meets_target"]]
    out = {"stages": stages, "target_p95_ms": args.target_ms, "capacity": max(ok) if ok else 0,
           "cores": stages[0]["resources"].get("cores") if stages else None}
    path = ROOT / "loadtest" / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.label}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("capacity:", out["capacity"], "concurrent vivas;", "results:", path)


if __name__ == "__main__":
    main()
