"""Load test: 50 / 100 / 150 concurrent vivas with audio bots, plus CPU and memory of the server.

Each stage starts N bots over `--ramp` seconds; every bot joins, consents and streams real-time 16 kHz audio for
`--duration` seconds, answering every question with synthesised speech. The server keeps doing everything it does
for real students: Silero VAD on every stream, encrypted incremental recording, the examiner engine, cached
examiner audio, transcript writes. Speech recognition on the server is the `stub` engine with a realistic delay
(STUB_STT_LATENCY_MS, default 250 ms ~ a hosted Whisper API), so the numbers measure Vaani itself; run a second
pass with STT_ENGINE=whisper-onnx to find the limit of CPU recognition on the same box.

    python loadtest/load.py --code LOAD26 --stages 50,100,150 --duration 240 --pid <server pid>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from loadtest import run_bots  # noqa: E402


class Monitor(threading.Thread):
    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.proc = psutil.Process(pid)
        self.samples: list[dict] = []
        self.stop_flag = False

    def run(self) -> None:
        procs = [self.proc] + self.proc.children(recursive=True)
        for p in procs:
            p.cpu_percent(None)
        psutil.cpu_percent(None)
        while not self.stop_flag:
            time.sleep(1.0)
            try:
                procs = [self.proc] + self.proc.children(recursive=True)
                cpu = sum(p.cpu_percent(None) for p in procs)
                rss = sum(p.memory_info().rss for p in procs)
                self.samples.append({"t": time.time(), "server_cpu_pct": cpu, "server_rss_mb": rss / 1e6,
                                     "machine_cpu_pct": psutil.cpu_percent(None), "threads": self.proc.num_threads()})
            except psutil.Error:
                break

    def summary(self) -> dict:
        s = self.samples[3:] or self.samples
        if not s:
            return {}
        cpu = sorted(x["server_cpu_pct"] for x in s)
        return {"server_cpu_avg_pct": round(sum(cpu) / len(cpu)), "server_cpu_p95_pct": round(cpu[int(len(cpu) * 0.95) - 1]),
                "server_rss_peak_mb": round(max(x["server_rss_mb"] for x in s)),
                "machine_cpu_avg_pct": round(sum(x["machine_cpu_pct"] for x in s) / len(s)), "cores": psutil.cpu_count()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8011")
    ap.add_argument("--code", required=True)
    ap.add_argument("--stages", default="50,100,150")
    ap.add_argument("--duration", type=float, default=240)
    ap.add_argument("--ramp", type=float, default=60)
    ap.add_argument("--pid", type=int, required=True, help="server process id (for CPU / memory)")
    ap.add_argument("--hints", action="store_true", default=True)
    ap.add_argument("--label", default="load")
    ap.add_argument("--pause", type=float, default=30)
    args = ap.parse_args()
    results = []
    for i, n in enumerate(int(x) for x in args.stages.split(",")):
        print(f"=== stage {n} bots, {args.duration}s", flush=True)
        mon = Monitor(args.pid)
        mon.start()
        ns = argparse.Namespace(base=args.base, code=args.code, bank="dbms", personas="strong,average,weak,idk,indian_english,rambler",
                                bots=n, ramp=args.ramp, max_s=args.duration, snr=None, mute_after=None, drop_at=None, drop_for=8.0,
                                hints=args.hints, no_fetch=False, roll_prefix=f"L{n}S{i}X", seed=100 + i, label=f"{args.label}-{n}")
        res = asyncio.run(run_bots.main_async(ns))
        mon.stop_flag = True
        mon.join(3)
        res["resources"] = mon.summary()
        res.pop("sessions", None)
        results.append(res)
        print(json.dumps({k: v for k, v in res.items() if k not in ("options",)}, indent=1), flush=True)
        time.sleep(args.pause)
    out = ROOT / "loadtest" / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.label}.json"
    out.write_text(json.dumps({"stages": results, "cores": psutil.cpu_count()}, indent=1), encoding="utf-8")
    print("results:", out)


if __name__ == "__main__":
    main()
