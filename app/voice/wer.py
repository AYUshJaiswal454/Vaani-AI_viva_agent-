"""Word error rate with a transparent text normaliser (used by the STT evaluation and the clip lab).

Normalisation follows common practice for ASR benchmarks (as in Whisper's English normaliser): lower-case, strip
punctuation, expand contractions ("I'll" -> "i will"), write numbers as words ("0.015" -> "zero point zero one five",
"150" -> "one hundred and fifty"), unify British/American spellings and drop hesitation sounds such as "um" and "uh",
so that formatting differences are not counted as errors. Both raw and normalised WER are reported.
"""
from __future__ import annotations

import re

FILLERS = {"um", "umm", "uh", "uhh", "hmm", "er", "erm", "ah", "mm"}
SPELL = {"normalisation": "normalization", "normalise": "normalize", "serialisable": "serializable", "colour": "color",
         "behaviour": "behavior", "optimisation": "optimization", "analyse": "analyze", "centre": "center",
         "freshwater": "fresh water", "e-mail": "email", "data base": "database", "per cent": "percent"}


SPELL.update({"behaviours": "behaviors", "colours": "colors", "favour": "favor", "honour": "honor", "labour": "labor",
              "neighbour": "neighbor", "metre": "meter", "litre": "liter", "fibre": "fiber", "modelling": "modeling",
              "travelling": "traveling", "programme": "program", "analysed": "analyzed", "catalogue": "catalog",
              "organise": "organize", "recognise": "recognize", "realise": "realize", "utilise": "utilize",
              "minimise": "minimize", "maximise": "maximize", "summarise": "summarize", "categorise": "categorize"})
CONTRACTIONS = {
    "i'll": "i will", "we'll": "we will", "you'll": "you will", "they'll": "they will", "it'll": "it will", "i'm": "i am",
    "we're": "we are", "you're": "you are", "they're": "they are", "i've": "i have", "we've": "we have", "you've": "you have",
    "they've": "they have", "it's": "it is", "that's": "that is", "there's": "there is", "what's": "what is", "he's": "he is",
    "she's": "she is", "let's": "let us", "don't": "do not", "doesn't": "does not", "didn't": "did not", "can't": "cannot",
    "won't": "will not", "isn't": "is not", "aren't": "are not", "wasn't": "was not", "weren't": "were not", "i'd": "i would",
    "couldn't": "could not", "shouldn't": "should not", "wouldn't": "would not", "hasn't": "has not", "haven't": "have not",
}
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    """0..999999 as words, British style ("one hundred and fifty")."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " and " + number_words(n % 100))
    if n < 1_000_000:
        return number_words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else (" and " if n % 1000 < 100 else " ") + number_words(n % 1000))
    return " ".join(_ONES[int(d)] for d in str(n))


def _spell_number(m: re.Match) -> str:
    whole, _, frac = m.group(0).partition(".")
    out = number_words(int(whole)) if len(whole) <= 6 else " ".join(_ONES[int(d)] for d in whole)
    if frac:
        out += " point " + " ".join(_ONES[int(d)] for d in frac)
    return f" {out} "


def normalise(text: str, drop_fillers: bool = True) -> list[str]:
    t = (text or "").lower().replace("’", "'")
    for a, b in CONTRACTIONS.items():
        t = re.sub(rf"\b{re.escape(a)}\b", b, t)
    for a, b in SPELL.items():
        t = t.replace(a, b)
    t = re.sub(r"(\w)isation", r"\1ization", t)
    t = t.replace("%", " percent ")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)                     # 2,500 -> 2500
    t = re.sub(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", _spell_number, t)
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
