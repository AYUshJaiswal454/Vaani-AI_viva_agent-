"""Word error rate with a transparent text normaliser (used by the STT evaluation and the clip lab).

Normalisation follows common practice for ASR benchmarks (lower-case, strip punctuation, spell out nothing, unify
British/American spellings, drop hesitation sounds such as "um" and "uh") so that formatting differences are not counted as errors.
Both raw and normalised WER are reported.
"""
from __future__ import annotations

import re

FILLERS = {"um", "umm", "uh", "uhh", "hmm", "er", "erm", "ah", "mm"}
SPELL = {"normalisation": "normalization", "normalise": "normalize", "serialisable": "serializable", "colour": "color",
         "behaviour": "behavior", "optimisation": "optimization", "analyse": "analyze", "centre": "center",
         "freshwater": "fresh water", "e-mail": "email", "data base": "database", "per cent": "percent"}


def normalise(text: str, drop_fillers: bool = True) -> list[str]:
    t = (text or "").lower().replace("’", "'")
    for a, b in SPELL.items():
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9'ऀ-ॿ ]+", " ", t)
    words = [w.strip("'") for w in t.split() if w.strip("'")]
    if drop_fillers:
        words = [w for w in words if w not in FILLERS]
    return words


def raw_words(text: str) -> list[str]:
    return re.sub(r"[^\w' ]+", " ", (text or "").lower()).split()


def edit_counts(ref: list[str], hyp: list[str]) -> tuple[int, int, int, int]:
    """(substitutions, deletions, insertions, reference length) by Levenshtein alignment."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, s, de, ins = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            s += ref[i - 1] != hyp[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            de += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return s, de, ins, n


def wer(ref: str, hyp: str, normalised: bool = True) -> dict:
    r = normalise(ref) if normalised else raw_words(ref)
    h = normalise(hyp) if normalised else raw_words(hyp)
    s, de, ins, n = edit_counts(r, h)
    return {"wer": (s + de + ins) / max(1, n), "sub": s, "del": de, "ins": ins, "ref_words": n}


def corpus_wer(pairs: list[tuple[str, str]], normalised: bool = True) -> dict:
    tot = {"sub": 0, "del": 0, "ins": 0, "ref_words": 0}
    for ref, hyp in pairs:
        w = wer(ref, hyp, normalised)
        for k in tot:
            tot[k] += w[k]
    tot["wer"] = (tot["sub"] + tot["del"] + tot["ins"]) / max(1, tot["ref_words"])
    return tot


def term_recall(ref: str, hyp: str, terms: list[str]) -> tuple[int, int]:
    """How many technical terms in the reference survived recognition (what matters for marking)."""
    r, h = " ".join(normalise(ref)), " ".join(normalise(hyp))
    present = [t for t in terms if " ".join(normalise(t)) in r]
    kept = [t for t in present if " ".join(normalise(t)) in h]
    return len(kept), len(present)
