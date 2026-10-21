"""Deterministic rubric scorer.

Works with no AI at all and doubles as an independent cross-check on the AI scorer: when the two disagree by more
than a set margin, the viva is flagged for mandatory faculty review. It scores only *content* signals (key points
covered, reasoning offered, problem-solving steps, project specifics); accent, grammar and Hindi-English mixing
cannot affect it because it never looks at them.
"""
from __future__ import annotations

from statistics import mean

from ..engine.text import normalize, tokens
from .rubric import DIMENSIONS, total_100
from .transcript import SUBSTANTIVE, Item, answer_of, group_items, line_id, mmss

RULE_VERSION = "rule-scorer-1.1"
THRESHOLDS = [(0.80, 5), (0.62, 4), (0.45, 3), (0.28, 2), (0.10, 1)]
GENERIC_COVERAGE = {"strong": 0.8, "partial": 0.5, "weak": 0.15}


def band(v: float | None) -> int:
    if v is None:
        return 0
    for th, s in THRESHOLDS:
        if v >= th:
            return s
    return 0


def _cov(turn: dict) -> float:
    a = answer_of(turn)
    if a.get("label") not in SUBSTANTIVE:
        return 0.0
    if a.get("generic"):
        return GENERIC_COVERAGE.get(a.get("label"), 0.0)
    c = float(a.get("coverage") or 0.0)
    return c * (0.75 if a.get("after_hint") else 1.0)


def _substantive(turns: list[dict]) -> list[dict]:
    return [t for t in turns if answer_of(t).get("label") in SUBSTANTIVE]


def item_main_coverage(item: Item) -> tuple[float, dict | None]:
    best, best_t = 0.0, None
    for t in item.main:
        c = _cov(t)
        if c > best or best_t is None:
            best, best_t = c, t
    return best, best_t


def _missing_points(item: Item) -> list[str]:
    covered: set[int] = set()
    for t in item.main:
        covered |= set(answer_of(t).get("covered") or [])
    return [kp["point"] for i, kp in enumerate(item.question.get("key_points", [])) if i not in covered]


def _covered_points(item: Item) -> list[str]:
    covered: set[int] = set()
    for t in item.main:
        covered |= set(answer_of(t).get("covered") or [])
    kps = item.question.get("key_points", [])
    return [kps[i]["point"] for i in sorted(covered) if i < len(kps)]


def _short(points: list[str], n: int = 2) -> str:
    cut = [p.split(":")[0].split("(")[0].strip().rstrip(".") for p in points[:n]]
    return ", ".join(c[0].lower() + c[1:] if c and not c[:2].isupper() else c for c in cut)


def _ev(turns: list[dict | None], k: int = 3) -> list[dict]:
    out, seen = [], set()
    for t in turns:
        if t is None or t["seq"] in seen:
            continue
        seen.add(t["seq"])
        out.append({"line": t["seq"], "t": round(t["t_start"], 1)})
        if len(out) >= k:
            break
    return out


def rule_score(turns: list[dict], questions: dict[str, dict], rubric: dict) -> dict:
    items = group_items(turns, questions)
    student = [t for t in turns if t["speaker"] == "student"]
    subst = _substantive(student)
    dims: dict[str, dict] = {}
    notes: list[str] = []

    # ---- conceptual: fundamentals + depth questions (not the project opener)
    concept_items = [i for i in items if i.phase in ("fundamentals", "depth") and i.question["type"] != "project"]
    covs = [(item_main_coverage(i), i) for i in concept_items]
    if covs:
        vals = [c for (c, _), _ in covs]
        best = max(covs, key=lambda x: x[0][0])
        worst = min(covs, key=lambda x: x[0][0])
        n_kp = sum(len(i.question.get("key_points", [])) for i in concept_items)
        n_cov = sum(len(_covered_points(i)) for i in concept_items)
        dims["conceptual"] = {
            "score": band(mean(vals)),
            "reason": f"Covered {n_cov} of {n_kp} key points across {len(concept_items)} questions; strongest at "
                      f"{mmss(best[1].asked_t)} ({best[1].question['topic']}), weakest at {mmss(worst[1].asked_t)} "
                      f"({worst[1].question['topic']}).",
            "evidence": _ev([best[0][1], worst[0][1]]), "signal": round(mean(vals), 3),
        }
    else:
        dims["conceptual"] = {"score": 0, "reason": "No fundamentals or depth question was answered.", "evidence": [],
                              "signal": 0.0}
        notes.append("no conceptual evidence")

    # ---- depth: follow-up answers + depth-phase questions + how often reasons are given
    fu_turns = [t for i in items if i.question["type"] != "project" for t in i.followups]
    fu_asked = sum(i.n_followups_asked for i in items if i.question["type"] != "project")
    fu_covs = [_cov(t) for t in fu_turns] + [0.0] * max(0, fu_asked - len(fu_turns))
    depth_items = [i for i in items if i.phase == "depth"]
    depth_main = [item_main_coverage(i)[0] for i in depth_items]
    reasoning_rate = (sum(1 for t in subst if (answer_of(t).get("reasoning") or 0) >= 1) / len(subst)) if subst else 0.0
    if fu_covs or depth_main:
        dv = 0.5 * (mean(fu_covs) if fu_covs else 0.0) + 0.3 * (mean(depth_main) if depth_main else 0.0) + 0.2 * reasoning_rate
        depth_turns = [t for i in depth_items for t in i.main if answer_of(t).get("label") in SUBSTANTIVE]
        good_fu = sorted(fu_turns, key=_cov, reverse=True) or sorted(depth_turns, key=_cov, reverse=True)
        weak_fu = sorted(fu_turns, key=_cov) or sorted(depth_turns, key=_cov)
        dims["depth"] = {
            "score": band(dv),
            "reason": f"{sum(1 for c in fu_covs if c >= 0.5)} of {len(fu_covs)} follow-ups answered with sound reasoning; "
                      f"reasons or examples given in {round(100 * reasoning_rate)}% of answers.",
            "evidence": _ev([*(good_fu[:2]), *(weak_fu[:1])]), "signal": round(dv, 3),
        }
    else:
        reasoned = [t for t in subst if (answer_of(t).get("reasoning") or 0) >= 1]
        dims["depth"] = {"score": band(0.2 * reasoning_rate),
                         "reason": f"No follow-up or depth question was reached; reasons given in {round(100 * reasoning_rate)}% of answers.",
                         "evidence": _ev(reasoned[:2]), "signal": round(0.2 * reasoning_rate, 3)}
        notes.append("no depth evidence")

    # ---- problem solving: the applied problem(s)
    applied = [i for i in items if i.phase == "applied"]
    if applied:
        vals, ev = [], []
        for i in applied:
            c, t = item_main_coverage(i)
            fu = [_cov(x) for x in i.followups]
            steps = max([answer_of(x).get("steps") or 0 for x in i.main] or [0])
            vals.append(min(1.0, 0.7 * c + 0.3 * (mean(fu) if fu else c) + (0.08 if steps >= 2 else 0.0)))
            ev += [t, *(i.followups[:1])]
        found = _covered_points(applied[0])
        missing = _missing_points(applied[0])
        dims["problem_solving"] = {
            "score": band(mean(vals)),
            "reason": (f"Applied problem: identified {len(found)} of {len(found) + len(missing)} expected steps"
                       + (f" ({_short(found)})" if found else "") + "."),
            "evidence": _ev(ev), "signal": round(mean(vals), 3),
        }
    else:
        dims["problem_solving"] = {"score": 0, "reason": "The viva did not reach the applied problem.", "evidence": [],
                                   "signal": None}
        notes.append("applied problem not reached")

    # ---- practical: project opener (+ project follow-up)
    project = [i for i in items if i.question["type"] == "project"]
    if project and _substantive(project[0].main):
        i = project[0]
        spec = max(answer_of(t).get("specificity") or 0.0 for t in i.main)
        cov, t = item_main_coverage(i)
        fu = [_cov(x) for x in i.followups]
        reasons = 1.0 if any((answer_of(x).get("reasoning") or 0) >= 1 for x in [*i.main, *i.followups]) else 0.0
        pv = 0.35 * spec + 0.25 * cov + 0.2 * (mean(fu) if fu else 0.5 * cov) + 0.2 * reasons
        toks = set(tokens(normalize(" ".join(x["text"] for x in i.main))))
        from ..engine.assess import TECH_WORDS
        techs = sorted(toks & TECH_WORDS)[:4]
        dims["practical"] = {
            "score": band(pv),
            "reason": ("Project described " + ("concretely" if spec >= 0.5 else "in general terms")
                       + (f" (named {', '.join(techs)})" if techs else " without naming technologies")
                       + ("; own role explained." if cov >= 0.5 else "; own contribution unclear.")),
            "evidence": _ev([t, *(i.followups[:1])]), "signal": round(pv, 3),
        }
    else:
        dims["practical"] = {"score": 0, "reason": "No project or practical work was described.", "evidence": [], "signal": 0.0}
        notes.append("no project evidence")

    # ---- communication: clarity and structure only (never accent, grammar or language mix)
    if subst:
        answers = [answer_of(t) for t in subst if answer_of(t).get("label") != "idk"]
        if answers:
            rel = mean(min(1.0, (a.get("relevance") or 0.0) / 0.6) for a in answers)
            structure = mean(1.0 if (a.get("reasoning") or 0) + (a.get("steps") or 0) >= 1 else 0.0 for a in answers)
            length_ok = mean(1.0 if 12 <= (a.get("words") or 0) <= 220 and not a.get("cut") else 0.0 for a in answers)
            off = mean(1.0 if a.get("label") == "offtopic" else 0.0 for a in answers)
            cv = max(0.0, 0.4 * rel + 0.3 * structure + 0.3 * length_ok - 0.3 * off)
            cuts = sum(1 for a in answers if a.get("cut"))
            clear = [t for t in subst if answer_of(t).get("label") in ("strong", "partial")]
            unclear = [t for t in subst if answer_of(t).get("label") in ("offtopic",) or answer_of(t).get("cut")]
            dims["communication"] = {
                "score": band(cv),
                "reason": f"{round(100 * (1 - off))}% of answers on the question; {round(100 * structure)}% organised with "
                          f"reasons, steps or examples" + (f"; {cuts} answer(s) ran past 90 seconds." if cuts else "."),
                "evidence": _ev([*(clear[:2]), *(unclear[:1])]), "signal": round(cv, 3),
            }
        else:
            dims["communication"] = {"score": 1, "reason": "Only 'I don't know' answers, so clarity could not be judged.",
                                     "evidence": _ev(subst[:2]), "signal": 0.2}
    else:
        dims["communication"] = {"score": 0, "reason": "No substantive answers to judge.", "evidence": [], "signal": 0.0}

    scores = {k: dims[k]["score"] for k in DIMENSIONS}
    return {"dims": {k: dims[k] for k in DIMENSIONS}, "total": total_100(scores, rubric),
            "feedback": feedback(items, dims, subst), "version": RULE_VERSION, "notes": notes,
            "evidence_counts": {"items": len(items), "answers": len(subst)}}


def feedback(items: list[Item], dims: dict, subst: list[dict]) -> list[dict]:
    """3-5 specific lines, each linked to the transcript."""
    out: list[dict] = []
    scored = [(item_main_coverage(i), i) for i in items if i.question["type"] != "project" and i.main]
    if scored:
        (bc, bt), bi = max(scored, key=lambda x: x[0][0])
        if bc >= 0.45 and bt is not None:
            pts = _covered_points(bi)
            out.append({"text": f"Strong on {bi.question['topic'].lower()}: at {mmss(bt['t_start'])} you covered "
                                f"{_short(pts, 3) or 'the main points'}.", "evidence": [line_id(bt["seq"])]})
        (wc, wt), wi = min(scored, key=lambda x: x[0][0])
        if wi is not bi and wc < 0.6 and wt is not None:
            miss = _missing_points(wi)
            out.append({"text": f"Revise {wi.question['topic'].lower()}: the answer at {mmss(wt['t_start'])} missed "
                                f"{_short(miss, 2) or 'the core idea'}.", "evidence": [line_id(wt["seq"])]})
    unanswered = [i for i in items if not _substantive(i.main) and i.question["type"] != "project"]
    if unanswered and len(out) < 3:
        u = unanswered[0]
        out.append({"text": f"No answer was given at {mmss(u.asked_t)} ({u.question['topic'].lower()}); revisit this topic.",
                    "evidence": [line_id(u.asked_seq)]})
    d = dims.get("depth", {})
    fu_ev = d.get("evidence") or []
    if d.get("score", 0) <= 2:
        out.append({"text": "When asked 'why' or 'what if', explain the reasoning and the trade-off, not only the definition.",
                    "evidence": [line_id(e["line"]) for e in fu_ev[-1:]] or ([line_id(subst[0]["seq"])] if subst else [])})
    elif d.get("score", 0) >= 4 and fu_ev:
        out.append({"text": "Good reasoning on follow-up questions: you explained why, not just what.",
                    "evidence": [line_id(fu_ev[0]["line"])]})
    ps = dims.get("problem_solving", {})
    applied = [i for i in items if i.phase == "applied" and i.main]
    if applied:
        a = applied[0]
        miss = _missing_points(a)
        found = _covered_points(a)
        c, t = item_main_coverage(a)
        if t is not None:
            if miss:
                out.append({"text": f"In the applied problem you " + (f"identified {_short(found, 1)}" if found else "made a start")
                                    + f"; also consider {_short(miss, 1)}.", "evidence": [line_id(t["seq"])]})
            else:
                out.append({"text": "You worked through the applied problem systematically, from cause to fix to verification.",
                            "evidence": [line_id(t["seq"])]})
    elif ps.get("signal") is None:
        out.append({"text": "The viva ended before the applied problem; practise answering more concisely to reach it.",
                    "evidence": []})
    com = dims.get("communication", {})
    off = [t for t in subst if answer_of(t).get("label") == "offtopic" or answer_of(t).get("cut")]
    if off and len(out) < 5:
        out.append({"text": f"Keep answers focused on the question: {len(off)} answer(s) drifted off topic or ran past 90 seconds.",
                    "evidence": [line_id(t["seq"]) for t in off[:2]]})
    elif com.get("score", 0) >= 4 and len(out) < 5:
        out.append({"text": "Your answers were clear and organised.", "evidence": [line_id(e["line"]) for e in com.get("evidence", [])[:1]]})
    if len(out) < 3:
        out.append({"text": "Use a concrete example in each answer; it makes your understanding easy to see.",
                    "evidence": [line_id(subst[-1]["seq"])] if subst else []})
    return out[:5]
