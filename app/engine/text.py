"""Text understanding for the live examiner: normalisation, key-point matching and intent detection.

Everything here is deterministic and fast (well under a millisecond per answer) so the examiner can decide its next
move without waiting for a language model. It is also deliberately accent- and dialect-neutral:

* Indian-English and Hinglish function words ("matlab", "toh", "na", "basically") are ignored for matching.
* Hinglish reasoning words ("kyunki", "isliye", "agar") count exactly like "because", "therefore", "if".
* "I don't know" is recognised in English and Hindi ("pata nahi", "yaad nahi"), so a student who answers in Hinglish
  gets the same no-penalty move-on as one who answers in English.
* Matching tolerates recogniser spelling slips (fuzzy match on long words) and British/American spelling.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

# ----------------------------------------------------------------------------- normalisation
_NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
              "eight": "8", "nine": "9", "ten": "10", "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th"}
_GENERIC_ALIASES = {
    "o of n": "o(n)", "order n": "o(n)", "big o": "big-o", "e g": "for example", "i e": "that is", "w r t": "with respect to",
    "can't": "cannot", "won't": "will not", "don't": "do not", "doesn't": "does not", "isn't": "is not", "it's": "it is",
    "i'm": "i am", "that's": "that is", "there's": "there is", "we're": "we are", "they're": "they are", "you're": "you are",
}
# Hinglish / Indian-English discourse words that carry no technical meaning.
HINGLISH_FILLERS = {
    "matlab", "yaani", "toh", "to", "na", "hai", "hain", "haan", "ha", "ji", "accha", "acha", "achha", "ki", "ka", "ke",
    "ko", "mein", "me", "se", "bhi", "woh", "wo", "ye", "yeh", "isko", "usko", "jo", "kya", "kaise", "aise", "waise",
    "basically", "actually", "like", "um", "umm", "uh", "uhh", "hmm", "er", "erm", "okay", "ok", "so", "right", "yeah",
    "sir", "maam", "ma'am", "madam", "hai na", "na", "bas", "thoda", "kuch", "wala", "wali", "vala",
}
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am", "of", "in", "on", "at", "for", "to", "and",
    "or", "it", "its", "this", "that", "these", "those", "with", "as", "by", "from", "we", "i", "you", "they", "he",
    "she", "them", "our", "my", "your", "their", "can", "will", "would", "should", "could", "do", "does", "did", "have",
    "has", "had", "there", "here", "what", "which", "who", "whom", "then", "than", "also", "very", "just", "about",
    "into", "if", "but", "not", "no", "yes", "all", "any", "some", "such", "each", "other", "more", "most", "one",
    "when", "where", "how", "why", "because", "so", "only", "same", "out", "up", "down", "over", "under", "again",
}
_SUFFIXES = sorted(["izations", "ization", "isations", "isation", "ational", "fulness", "iveness", "ations", "ation",
                    "ments", "ment", "ness", "ings", "ing", "ized", "ised", "izes", "ises", "ize", "ise", "edly", "ed",
                    "ies", "es", "s", "ly", "ers", "er", "ities", "ity", "ive", "able", "ible", "al"], key=len, reverse=True)

_DEVANAGARI = {  # rough romanisation so English technical words written in Devanagari still match
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n", "ट": "t",
    "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "f",
    "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri", "ं": "n",
    "ँ": "n", "ः": "h", "्": "", "़": "", "ॉ": "o", "ॅ": "e", "।": ".", "क़": "q", "ज़": "z", "फ़": "f",
}
_CONSONANTS = set("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह")
_MATRAS = set("ािीुूेैोौृंँः्ॉॅ")


def romanize_devanagari(text: str) -> str:
    if not re.search(r"[ऀ-ॿ]", text):
        return text
    out = []
    for i, ch in enumerate(text):
        rom = _DEVANAGARI.get(ch)
        if rom is None:
            out.append(ch)
            continue
        out.append(rom)
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch in _CONSONANTS and nxt not in _MATRAS and nxt and re.match(r"[ऀ-ॿ]", nxt):
            out.append("a")       # inherent vowel inside a word
    return "".join(out)


def _alternation(keys) -> str:
    return "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))


_GENERIC_RX = re.compile(rf"(?<![\w'])({_alternation(_GENERIC_ALIASES)})(?![\w'])")
_alias_keys: dict[int, tuple[dict, tuple]] = {}
_alias_rx: dict[tuple, tuple[re.Pattern, dict]] = {}


def _alias_key(aliases: dict[str, str] | None) -> tuple:
    """A hashable, cached form of a bank's alias table (the same dict object is reused for a whole viva)."""
    if not aliases:
        return ()
    hit = _alias_keys.get(id(aliases))
    if hit is not None and hit[0] is aliases:
        return hit[1]
    key = tuple(sorted((k.lower(), v.lower()) for k, v in aliases.items() if k.strip()))
    if len(_alias_keys) > 512:
        _alias_keys.clear()
    _alias_keys[id(aliases)] = (aliases, key)
    return key


def normalize(text: str, aliases: dict[str, str] | None = None) -> str:
    """Lower-case, romanise Devanagari, expand contractions and subject aliases (e.g. "3nf" -> "third normal form").

    Called hundreds of times per answer (every key-point term too), so results are cached and each alias table is
    compiled into one regular expression once (measured: this was 20% of server CPU under load before)."""
    return _normalize(text or "", _alias_key(aliases))


@lru_cache(maxsize=100_000)
def _normalize(text: str, key: tuple) -> str:
    t = unicodedata.normalize("NFKC", romanize_devanagari(text)).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    t = _GENERIC_RX.sub(lambda m: _GENERIC_ALIASES[m.group(1)], t)
    if key:
        rx = _alias_rx.get(key)
        if rx is None:
            table = dict(key)
            rx = _alias_rx[key] = (re.compile(rf"(?<![\w])({_alternation(table)})(?![\w])"), table)
        t = rx[0].sub(lambda m: rx[1][m.group(1)], t)
    t = re.sub(r"[^\w\s'+#().-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'+#.-]*", text):
        w = w.strip(".-'")
        if w:
            out.append(_NUM_WORDS.get(w, w))
    return out


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    w = word.lower().replace("'s", "")
    if len(w) <= 3 or not w.isalpha():
        return w
    w = w.replace("isation", "ization").replace("yse", "yze")
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= (3 if suf in ("s", "es", "ies", "ed") else 4):
            base = w[: -len(suf)]
            if suf == "ies":
                base += "y"
            elif suf in ("es",) and not base.endswith(("s", "x", "z", "ch", "sh")):
                base = w[:-1]                           # "indexes"->"index" but "databases"->"database"
            if base.endswith(("ss", "us", "is")) and suf == "s":
                return w
            return base
    return w


def content_tokens(text: str, aliases: dict[str, str] | None = None) -> list[str]:
    return [t for t in tokens(normalize(text, aliases)) if t not in STOPWORDS and t not in HINGLISH_FILLERS]


def _ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if abs(la - lb) > max(la, lb) * 0.3:
        return 0.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return 1.0 - prev[lb] / max(la, lb)


def word_match(a: str, b: str) -> bool:
    """Do two words mean the same term? Equal stems, shared long prefix, or a near-spelling (recogniser slip)."""
    if a == b or (a.endswith("s") and a[:-1] == b) or (b.endswith("s") and b[:-1] == a):
        return True
    sa, sb = stem(a), stem(b)
    if sa == sb:
        return True
    if min(len(sa), len(sb)) >= 5 and (sa.startswith(sb) or sb.startswith(sa)):
        return True
    return min(len(a), len(b)) >= 6 and _ratio(a, b) >= 0.84


def phrase_in(phrase_toks: list[str], text_toks: list[str], max_gap: int = 2) -> bool:
    """Is the phrase present in order, allowing up to `max_gap` extra words between its words?"""
    if not phrase_toks:
        return False
    n = len(text_toks)
    for start in range(n):
        if not word_match(phrase_toks[0], text_toks[start]):
            continue
        pos, ok = start, True
        for pt in phrase_toks[1:]:
            for k in range(pos + 1, min(n, pos + 2 + max_gap)):
                if word_match(pt, text_toks[k]):
                    pos = k
                    break
            else:
                ok = False
                break
        if ok:
            return True
    return False


_ARTICLES = {"a", "an", "the"}


def term_tokens(term: str, aliases: dict[str, str] | None = None) -> list[str]:
    """A key-point term as a phrase. Only articles are dropped: "can change" must not shrink to "change"."""
    toks = tokens(normalize(term, aliases))
    kept = [t for t in toks if t not in _ARTICLES]
    return kept or toks


# ----------------------------------------------------------------------------- intents
_IDK = re.compile(
    r"\b(i\s*(do\s*not|don'?t|dont)\s*(really\s*)?(know|remember|recall)|no\s*idea|not\s*sure|i\s*have\s*no\s*clue|"
    r"(i\s*)?can(not|'?t)\s*(remember|recall|answer)|i\s*forgot|don'?t\s*know|i\s*am\s*not\s*aware|"
    r"pata\s*nahi|nahi\s*pata|nahin\s*pata|pata\s*nahin|maloom\s*nahi|yaad\s*nahi|nahi\s*aata|nahi\s*aati|idea\s*nahi|"
    r"(can\s*we\s*|let'?s\s*|please\s*)skip|skip\s*(this|it)|next\s*question|let'?s\s*move\s*on)\b", re.I)
_REPEAT = re.compile(
    r"\b(repeat|say\s*(that|it)\s*again|come\s*again|pardon|sorry\s*\??$|i\s*did(n'?t| not)\s*(hear|get|catch)|"
    r"(could|can)\s*you\s*(please\s*)?(say|ask)|once\s*more|phir\s*se|fir\s*se|dobara|repeat\s*karo|"
    r"didn'?t\s*understand\s*the\s*question)\b", re.I)
_CLARIFY = re.compile(
    r"\b(what\s*do\s*you\s*mean|what\s*does\s*.{0,30}\s*mean\s*here|can\s*you\s*(explain|clarify)\s*(the|your)?\s*question|"
    r"(do|are)\s*you\s*(mean|asking)|which\s*one\s*do\s*you\s*mean|in\s*what\s*context|samjha\s*nahi|samajh\s*nahi)\b", re.I)
_THINK = re.compile(
    r"\b(let\s*me\s*think|give\s*me\s*a\s*(second|sec|moment|minute)|one\s*(second|sec|moment|minute)|hold\s*on|"
    r"just\s*a\s*(second|sec|moment|minute)|thinking|ek\s*(minute|second|sec|min)|sochne\s*do|soch\s*(ke|kar)\s*batata)\b",
    re.I)
INJECTION = re.compile(
    r"(ignore\s*(all|any|the|your)?\s*(previous|prior|above|earlier)?\s*(instructions|rules|prompt)|"
    r"(system|developer)\s*prompt|you\s*are\s*now|act\s*as\s*(a|an|my)|pretend\s*(to\s*be|you)|jailbreak|"
    r"(give|award|assign)\s*(me\s*)?(full|maximum|max|100|ten|10|five|5)\s*(marks|points|score|out\s*of)|"
    r"(mark|score|grade)\s*me\s*(full|high|5|10|100)|override\s*(the\s*)?(rubric|score)|new\s*instructions|"
    r"reveal\s*(the\s*)?(answer|rubric|prompt)|tell\s*me\s*the\s*(correct\s*)?answer)", re.I)
REASONING = re.compile(
    r"\b(because|since|therefore|hence|thus|so\s*that|which\s*means|that\s*is\s*why|that'?s\s*why|otherwise|whereas|"
    r"however|but\s*if|if\s*.{1,60}\s*then|for\s*example|for\s*instance|e\.?g\.?|such\s*as|trade-?off|on\s*the\s*other\s*hand|"
    r"compared\s*to|in\s*contrast|as\s*a\s*result|the\s*reason|due\s*to|leads?\s*to|by\s*using|to\s*avoid|in\s*order\s*to|"
    r"so\s*as\s*to|instead\s*of|kyunki|kyuki|isliye|agar|jaise|lekin|"
    r"warna|isse|is\s*wajah\s*se)\b", re.I)
_STEPS = re.compile(r"\b(first(ly)?|second(ly)?|then|next|after\s*that|finally|step|lastly|pehle|phir|uske\s*baad)\b", re.I)
EVALUATIVE = re.compile(r"\b(correct|incorrect|right answer|wrong|exactly|perfect|well done|great answer|good answer|"
                        r"that'?s right|not quite|almost|close but|you got it|spot on|excellent)\b", re.I)
_INCOMPLETE_END = re.compile(r"(\b(and|or|but|so|because|like|which|that|the|a|an|of|to|is|are|was|if|then|when|um+|uh+|"
                             r"er+|basically|matlab|toh|aur|ki|ke)\s*[,.]?\s*$)", re.I)   # not a bare trailing comma: Whisper adds one
                                                                  # whenever speech ends on a level pitch


@dataclass
class Intent:
    idk: bool = False
    repeat: bool = False
    clarify: bool = False
    think: bool = False
    injection: bool = False
    words: int = 0
    reasoning_markers: list[str] = field(default_factory=list)
    step_markers: int = 0


def detect_intent(text: str) -> Intent:
    t = normalize(text)
    n = len(t.split())
    markers = sorted({m.group(0).lower() for m in REASONING.finditer(t)})
    short = n <= 14
    return Intent(
        idk=bool(_IDK.search(t)),
        repeat=bool(_REPEAT.search(t)) and short,
        clarify=bool(_CLARIFY.search(t)) and n <= 25,
        think=bool(_THINK.search(t)) and n <= 10,
        injection=bool(INJECTION.search(t)),
        words=n,
        reasoning_markers=markers,
        step_markers=len(_STEPS.findall(t)),
    )


def looks_incomplete(text: str) -> bool:
    """Did the student stop mid-sentence ("... and", "because", "um")? Used to wait a little longer before replying."""
    t = (text or "").strip()
    if not t:
        return True
    return bool(_INCOMPLETE_END.search(t)) or len(t.split()) <= 2 and not detect_intent(t).idk


def strip_injection(text: str) -> str:
    return INJECTION.sub(" [ignored instruction] ", text or "")


def filler_ratio(text: str) -> float:
    toks = tokens(normalize(text))
    if not toks:
        return 0.0
    fillers = {"um", "umm", "uh", "uhh", "hmm", "er", "erm", "like", "basically", "actually", "matlab", "toh"}
    return sum(1 for t in toks if t in fillers) / len(toks)
