"""Speech-recognition accuracy on Indian-accented English (and your own clips).

Default dataset: the "pure" set of NPTEL2020 (AI4Bharat): 1,000 hand-corrected clips of Indian professors
lecturing in English on technical subjects, the closest public match to a viva. The script downloads it from
GitHub on first use (189 MB, not redistributed here) and evaluates a deterministic sample of clips.

    python scripts/eval_stt.py --n 100 --engines whisper-onnx:tiny.en,whisper-onnx:base.en
    python scripts/eval_stt.py --clips my_clips/manifest.csv --engines groq,sarvam     # path,reference,language
    python scripts/eval_stt.py --lab                                                    # clips recorded in the Speech lab

Writes docs/data/stt_eval.json and prints a Markdown table (WER, technical-term recall, speed).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import tarfile
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NPTEL_URL = "https://github.com/AI4Bharat/NPTEL2020-Indian-English-Speech-Dataset/releases/download/v0.1/nptel-pure-set.tar.gz"


def nptel_dir(base: Path) -> Path:
    d = base / "nptel-pure"
    if (d / "wav").exists():
        return d
    base.mkdir(parents=True, exist_ok=True)
    arc = base / "nptel-pure-set.tar.gz"
    if not arc.exists():
        print("downloading NPTEL2020 pure set (189 MB)…")
        urllib.request.urlretrieve(NPTEL_URL, arc)
    with tarfile.open(arc) as t:
        t.extractall(base, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(base)
    return d


def load_wav(path: Path) -> bytes:
    from app.voice.audio import read_wav

    return read_wav(path.read_bytes())[0]


def clips_from(args) -> list[dict]:
    if args.clips:
        out = []
        base = Path(args.clips).parent
        with open(args.clips, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out.append({"id": row["path"], "path": base / row["path"], "reference": row["reference"],
                            "language": row.get("language") or "en-IN"})
        return out
    if args.lab:
        from app.config import get_settings
        from app.db import get_db

        rows = get_db().fetchall("SELECT * FROM stt_clips ORDER BY id")
        return [{"id": f"lab-{r['id']}", "lab_path": get_settings().data_dir / r["audio_path"], "reference": r["reference"],
                 "language": r["language"]} for r in rows]
    d = nptel_dir(Path(args.datasets))
    wavs = sorted((d / "wav").glob("*.wav"))
    step = max(1, len(wavs) // args.n)
    out = []
    for w in wavs[::step][: args.n]:
        ref = (d / "corrected_txt" / (w.stem + ".txt")).read_text(encoding="utf-8").strip()
        if len(ref.split()) < 4:
            continue
        out.append({"id": w.stem[:12], "path": w, "reference": ref, "language": "en-IN"})
    return out


async def run_engine(spec: str, clips: list[dict], prompt: str) -> dict:
    from app.config import get_settings
    from app.voice import stt
    from app.voice.audio import read_secure, read_wav
    from app.voice.wer import corpus_wer, wer

    engine, _, model = spec.partition(":")
    if engine == "whisper-onnx" and model:
        get_settings().whisper_model = model
    rows, pairs, raw_pairs, total_audio, total_ms = [], [], [], 0.0, 0
    for c in clips:
        pcm = read_wav(read_secure(c["lab_path"]))[0] if c.get("lab_path") else load_wav(c["path"])
        t0 = time.perf_counter()
        res = await stt.transcribe(pcm, engine=engine, language="en", prompt=prompt)
        ms = int((time.perf_counter() - t0) * 1000)
        total_ms += ms
        total_audio += len(pcm) / 32000
        w = wer(c["reference"], res.text)
        pairs.append((c["reference"], res.text))
        raw_pairs.append((c["reference"], res.text))
        rows.append({"clip": c["id"], "language": c["language"], "seconds": round(len(pcm) / 32000, 2), "wer": round(w["wer"], 3),
                     "reference": c["reference"], "hypothesis": res.text, "confidence": res.confidence, "ms": ms, "error": res.error})
    agg = corpus_wer(pairs)
    raw = corpus_wer(raw_pairs, normalised=False)
    return {"engine": spec, "clips": len(clips), "audio_minutes": round(total_audio / 60, 1), "wer": round(agg["wer"], 4),
            "wer_raw": round(raw["wer"], 4), "sub": agg["sub"], "del": agg["del"], "ins": agg["ins"], "ref_words": agg["ref_words"],
            "rtf": round(total_ms / 1000 / max(1e-6, total_audio), 3), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="whisper-onnx:base.en")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--datasets", default=os.environ.get("DATASETS_DIR", str(ROOT / "data" / "datasets")))
    ap.add_argument("--clips", help="CSV manifest with path,reference[,language]")
    ap.add_argument("--lab", action="store_true", help="use clips recorded in the Speech lab")
    ap.add_argument("--prompt", default="", help="vocabulary prompt passed to every engine")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "stt_eval.json"))
    args = ap.parse_args()
    os.environ.setdefault("SEED_DEMO", "false")
    clips = clips_from(args)
    print(f"{len(clips)} clips")
    results = []
    for spec in args.engines.split(","):
        r = asyncio.run(run_engine(spec.strip(), clips, args.prompt))
        results.append(r)
        print(f"{spec}: WER {r['wer']:.1%} (raw {r['wer_raw']:.1%}), RTF {r['rtf']}")
    dataset = "custom clips" if args.clips else ("speech lab clips" if args.lab else "NPTEL2020 pure set (Indian-accented English lectures)")
    out = {"dataset": dataset, "n_clips": len(clips), "results": results, "generated": time.strftime("%Y-%m-%d %H:%M"),
           "cpu": os.cpu_count()}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n| Engine | Clips | Audio (min) | WER | WER (raw text) | Real-time factor |\n|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['engine']} | {r['clips']} | {r['audio_minutes']} | {r['wer']:.1%} | {r['wer_raw']:.1%} | {r['rtf']} |")
    print("written", args.out)


if __name__ == "__main__":
    main()
