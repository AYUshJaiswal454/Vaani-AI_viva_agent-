"""Scoring evaluation: accuracy against reference marks, consistency over repeated runs, fairness pairs.

    python scripts/eval_scoring.py                 # rule scorer, 14 persona vivas (offline)
    python scripts/eval_scoring.py --llm --runs 5  # also the AI scorer (needs LLM_PROVIDER + key), 5 runs per transcript
    python scripts/eval_scoring.py --from-db       # AI score vs faculty marks entered in the app (Calibration)

Writes docs/data/scoring_eval.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VIVAS = [("strong", 1), ("strong", 2), ("indian_english", 1), ("hinglish", 1), ("average", 1), ("average", 2), ("average", 3),
         ("rambler", 1), ("injector", 1), ("silent", 1), ("offtopic", 1), ("weak", 1), ("weak", 2), ("idk", 1)]


def pearson(a, b):
    from app.scoring.service import pearson as p

    return p(a, b)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "scoring_eval.json"))
    args = ap.parse_args()
    os.environ.setdefault("SEED_DEMO", "false")
    from app.scoring.rubric import DIMENSIONS, default_rubric, total_100
    from app.scoring.rule_scorer import rule_score
    from tests.personas import PERSONAS
    from tests.sim import run_viva
    from tests.support import approved_questions

    if args.from_db:
        from app.scoring.service import calibration

        c = calibration()
        print(json.dumps({k: v for k, v in c.items() if k != "pairs"}, indent=1))
        return
    rubric = default_rubric()
    ref = json.loads((ROOT / "tests" / "fixtures" / "reference_marks.json").read_text())["personas"]
    qs, aliases = approved_questions("dbms")
    qmap = {q["code"]: q for q in qs}
    rows = []
    llm_rows = []
    for persona, seed in VIVAS:
        r = run_viva(qs, PERSONAS[persona](seed=seed), aliases=aliases, seed=f"eval:{persona}:{seed}")
        sc = [rule_score(r["turns"], qmap, rubric) for _ in range(args.runs)]
        totals = [s["total"] for s in sc]
        refd = {k: ref[persona][k] for k in DIMENSIONS}
        reft = total_100(refd, rubric)
        rows.append({"persona": persona, "seed": seed, "duration_s": r["duration"], "reference_total": reft,
                     "rule_total": totals[0], "rule_runs_sd": round(statistics.pstdev(totals), 3),
                     "rule_dims": {k: sc[0]["dims"][k]["score"] for k in DIMENSIONS}, "reference_dims": refd})
        if args.llm:
            from app.scoring.llm_scorer import llm_score

            res = [asyncio.run(llm_score(r["turns"], qmap, rubric, "Database Management Systems", runs=1)) for _ in range(args.runs)]
            res = [x for x in res if x and not x.get("error")]
            if res:
                t = [x["total"] for x in res]
                llm_rows.append({"persona": persona, "seed": seed, "reference_total": reft, "ai_totals": t,
                                 "ai_mean": round(statistics.mean(t), 1), "ai_sd": round(statistics.pstdev(t), 2),
                                 "dim_sd": {k: round(statistics.pstdev([x["dims"][k]["score"] for x in res]), 2) for k in DIMENSIONS},
                                 "model": res[0]["model"]})
        print(f"{persona:15s} ref {reft:5.1f}  rule {totals[0]:5.1f}" + (f"  ai {llm_rows[-1]['ai_mean']:5.1f} sd {llm_rows[-1]['ai_sd']}" if llm_rows and llm_rows[-1]["persona"] == persona else ""))

    def summary(pred_key: str, data: list[dict]) -> dict:
        p = [d[pred_key] for d in data]
        t = [d["reference_total"] for d in data]
        gaps = [abs(a - b) for a, b in zip(p, t)]
        return {"n": len(data), "mae_total": round(statistics.mean(gaps), 2), "max_gap": round(max(gaps), 1),
                "within_10": round(sum(1 for g in gaps if g <= 10) / len(gaps), 2), "pearson": round(pearson(p, t), 3)}

    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "reference": "synthetic persona reference marks (tests/fixtures/reference_marks.json)",
           "rule_scorer": summary("rule_total", rows), "rows": rows,
           "rule_dim_mae": {k: round(statistics.mean(abs(r["rule_dims"][k] - r["reference_dims"][k]) for r in rows), 2) for k in DIMENSIONS},
           "rule_consistency": {"runs_per_transcript": args.runs, "max_sd": max(r["rule_runs_sd"] for r in rows)}}
    if llm_rows:
        out["ai_scorer"] = summary("ai_mean", llm_rows)
        out["ai_consistency"] = {"runs_per_transcript": args.runs, "mean_sd_total": round(statistics.mean(r["ai_sd"] for r in llm_rows), 2),
                                 "max_sd_total": max(r["ai_sd"] for r in llm_rows), "model": llm_rows[0]["model"]}
        out["ai_rows"] = llm_rows
    # fairness: same content, different language style
    by = {(r["persona"], r["seed"]): r for r in rows}
    s, ie, hi = by[("strong", 1)], by[("indian_english", 1)], by[("hinglish", 1)]
    out["fairness"] = {
        "strong_vs_indian_english_total_gap": round(abs(s["rule_total"] - ie["rule_total"]), 1),
        "strong_vs_indian_english_max_content_dim_gap": max(abs(s["rule_dims"][k] - ie["rule_dims"][k]) for k in
                                                           ("conceptual", "depth", "problem_solving", "practical")),
        "hinglish_total": hi["rule_total"], "hinglish_reference": hi["reference_total"],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows", "ai_rows")}, indent=1))


if __name__ == "__main__":
    main()
