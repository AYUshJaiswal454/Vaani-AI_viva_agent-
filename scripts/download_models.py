"""Download the local speech models (no API key needed afterwards).

    python scripts/download_models.py                    # Silero VAD + Whisper base.en (recommended, ~200 MB)
    python scripts/download_models.py --whisper tiny.en  # smallest/fastest English model (~120 MB)
    python scripts/download_models.py --whisper base     # multilingual (for Hindi / Hinglish answers)

Models come from the sherpa-onnx release page on GitHub (Whisper exported to ONNX, Apache-2.0/MIT licences) and
are stored in ./models (or MODELS_DIR). Only the int8 files are kept to save space.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
WHISPER = ("tiny.en", "base.en", "small.en", "tiny", "base", "small", "distil-small.en")


def fetch(url: str, dest: Path) -> None:
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("content-length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / total:6.1%} of {total / 1e6:.0f} MB", end="", flush=True)
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", default="base.en", choices=WHISPER)
    ap.add_argument("--dir", default=os.environ.get("MODELS_DIR", str(ROOT / "models")))
    ap.add_argument("--keep-fp32", action="store_true", help="also keep the larger float32 files")
    args = ap.parse_args()
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    vad = out / "silero_vad.onnx"
    if not vad.exists():
        fetch(f"{RELEASE}/silero_vad.onnx", vad)
    target = out / f"sherpa-onnx-whisper-{args.whisper}"
    if (target / f"{args.whisper}-encoder.int8.onnx").exists():
        print(f"whisper {args.whisper} already present in {target}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        arc = Path(tmp) / "model.tar.bz2"
        fetch(f"{RELEASE}/sherpa-onnx-whisper-{args.whisper}.tar.bz2", arc)
        with tarfile.open(arc) as t:
            members = [m for m in t.getmembers() if m.isfile() and (args.keep_fp32 or ".int8." in m.name or m.name.endswith("tokens.txt"))
                       and "test_wavs" not in m.name]
            t.extractall(tmp, members=members)
        src = Path(tmp) / f"sherpa-onnx-whisper-{args.whisper}"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(src), str(target))
    print(f"ready: {target}")
    print("Set STT_ENGINE=whisper-onnx and WHISPER_MODEL=" + args.whisper + " (the server picks it up automatically if present).")


if __name__ == "__main__":
    sys.exit(main())
