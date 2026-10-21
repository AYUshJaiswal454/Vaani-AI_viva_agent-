"""Export finished vivas from a Vaani data directory to samples/vivas/ (audio, transcript, scores, audit data).

    python scripts/export_samples.py --data-dir ./data --results loadtest/results/<time>-samples.json
    python scripts/export_samples.py --data-dir ./data --sessions 1,2,3

With --results, sessions are taken from a run_bots.py result file and named after their persona.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--results", help="run_bots.py result JSON (labels sessions by persona)")
    ap.add_argument("--sessions", help="comma-separated session ids")
    ap.add_argument("--out", default=str(ROOT / "samples" / "vivas"))
    args = ap.parse_args()
    os.environ["DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["SEED_DEMO"] = "false"
    from app.samples import export_session

    pairs: list[tuple[int, str]] = []
    if args.results:
        res = json.loads(Path(args.results).read_text())
        pairs = [(s["session_id"], s["persona"]) for s in res["sessions"] if s.get("session_id")]
    if args.sessions:
        pairs += [(int(x), "") for x in args.sessions.split(",")]
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    index = []
    for i, (sid, label) in enumerate(pairs, 1):
        info = export_session(sid, out / f"{i:02d}-{label or 'session'}", label)
        index.append(info)
        print(f"{info['dir']}: {info['student']} total {info['total']} ({info['turns']} turns)")
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
