"""Live answer assessment: which key points did the student cover, and what kind of answer was it?

This runs on every answer in well under a millisecond. It only decides the examiner's next move (go deeper, give a
hint, move on, redirect); marks are given later by the scorer on the full transcript. Labels:

  strong    covers most key points -> go deeper
  partial   covers some key points -> one hint, then move on
  weak      on topic but no key point (vague or wrong) -> treated like partial
  idk       "I don't know" (English or Hindi) with nothing else -> move on, no penalty spiral
  offtopic  unrelated to the question and the subject -> redirect once
  repeat / clarify / think   requests, not answers
  empty     nothing usable was heard
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .text import (HINGLISH_FILLERS, STOPWORDS, content_tokens, detect_intent, filler_ratio, normalize, phrase_in,
                   strip_injection, term_tokens, tokens)

TECH_WORDS = {
    "python", "java", "javascript", "typescript", "react", "node", "nodejs", "express", "django", "flask", "spring", "html",
    "css", "sql", "mysql", "postgres", "postgresql", "mongodb", "sqlite", "firebase", "redis", "oracle", "docker", "aws",
    "api", "rest", "graphql", "android", "kotlin", "flutter", "c++", "c#", "golang", "rust", "linux", "git", "github",
    "kubernetes", "tensorflow", "pytorch", "pandas", "numpy", "opencv", "arduino", "raspberry", "socket", "thread",
    "jwt", "oauth", "nginx", "kafka", "rabbitmq", "elasticsearch", "fastapi", "next.js", "nextjs", "vue", "angular",
    "bootstrap", "tailwind", "supabase", "dynamodb", "bigquery", "spark", "hadoop", "cassandra", "neo4j", "prisma",
}
ACTION_WORDS = {"built", "designed", "implemented", "wrote", "created", "developed", "made", "deployed", "handled",
                "optimised", "optimized", "fixed", "debugged", "integrated", "tested", "led", "added", "migrated",
                # Hinglish: "maine banaya", "likha", "design kiya" count exactly like their English equivalents
                "banaya", "banayi", "banaye", "bnaya", "likha", "likhi", "likhe", "kiya", "kari", "karaya", "sambhala"}
FIRST_PERSON = {"i", "my", "we", "our", "me", "maine", "mera", "meri", "mere", "humne", "hamne", "hamara", "hamari", "mujhe"}


@dataclass
class Assessment:
    label: str
    coverage: float = 0.0
    covered: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    misconception: str = ""
    misconception_hint: str = ""
    reasoning: int = 0
    steps: int = 0
    words: int = 0
    relevance: float = 0.0
    injection: bool = False
    idk: bool = False
    specificity: float = 0.0
    fillers: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coverage"] = round(self.coverage, 3)
        d["relevance"] = round(self.relevance, 3)
        d["specificity"] = round(self.specificity, 3)
        d["fillers"] = round(self.fillers, 3)
        return d


def match_points(answer: str, key_points: list[dict], aliases: dict | None = None) -> tuple[list[int], list[str], float]:
    """Indices of key points whose terms appear in the answer, the terms found, and weighted coverage (0..1)."""
    toks = tokens(normalize(answer, aliases))
    covered, terms_found = [], []
    total_w = sum(float(kp.get("weight", 1.0)) for kp in key_points) or 1.0
    got_w = 0.0
    for i, kp in enumerate(key_points):
        for term in kp.get("terms") or [kp.get("point", "")]:
            tt = term_tokens(term, aliases)
            if tt and phrase_in(tt, toks, max_gap=2):
                covered.append(i)
                terms_found.append(term)
                got_w += float(kp.get("weight", 1.0))
                break
    return covered, terms_found, min(1.0, got_w / total_w)


def relevance(answer: str, vocab: set[str], question_text: str, aliases: dict | None = None) -> float:
    """Share of the answer's content words that belong to the subject (bank vocabulary or the question)."""
    words = content_tokens(answer, aliases)
    if not words:
        return 0.0
    qwords = set(content_tokens(question_text, aliases))
    hits = sum(1 for w in words if w in vocab or w in qwords or w in TECH_WORDS)
    return hits / len(words)


def specificity(answer: str) -> float:
    """How concrete is a project description? Named technologies, first-person actions, numbers (0..1)."""
    toks = tokens(normalize(answer))
    if not toks:
        return 0.0
    tech = len({t for t in toks if t in TECH_WORDS})
    actions = len({t for t in toks if t in ACTION_WORDS})
    numbers = len({t for t in toks if any(c.isdigit() for c in t)})
    first_person = sum(1 for t in toks if t in FIRST_PERSON)
    score = min(1.0, 0.18 * tech) + min(0.5, 0.12 * actions) + min(0.2, 0.07 * numbers) + min(0.2, 0.04 * first_person)
    return min(1.0, score)


def assess(answer: str, *, key_points: list[dict], question_text: str, vocab: set[str], aliases: dict | None = None,
           misconceptions: list[dict] | None = None, is_project: bool = False, generic_probe: bool = False) -> Assessment:
    raw = (answer or "").strip()
    intent = detect_intent(raw)
    clean = strip_injection(raw) if intent.injection else raw
    words = [w for w in tokens(normalize(clean, aliases)) if w not in HINGLISH_FILLERS]
    covered, terms, cov = match_points(clean, key_points, aliases) if key_points else ([], [], 0.0)
    rel = relevance(clean, vocab, question_text, aliases)
    mis_text, mis_hint = "", ""
    toks = tokens(normalize(clean, aliases))
    for m in misconceptions or []:
        for term in m.get("terms") or [m.get("text", "")]:
            tt = term_tokens(term, aliases)
            if tt and phrase_in(tt, toks, max_gap=1):
                mis_text, mis_hint = m.get("text", ""), m.get("hint", "")
                break
        if mis_text:
            break
    a = Assessment(label="", coverage=cov, covered=covered, missing=[i for i in range(len(key_points)) if i not in covered],
                   misconception=mis_text, misconception_hint=mis_hint, reasoning=len(intent.reasoning_markers),
                   steps=intent.step_markers, words=len(words), relevance=rel, injection=intent.injection,
                   idk=intent.idk, specificity=specificity(clean) if is_project else 0.0, fillers=filler_ratio(raw),
                   matched_terms=terms)
    content_words = [w for w in words if w not in STOPWORDS]
    if not raw or (len(content_words) == 0 and not intent.idk and not intent.repeat and not intent.think):
        a.label = "empty"
    elif intent.repeat and not covered:
        a.label = "repeat"
    elif intent.think and not covered:
        a.label = "think"
    elif intent.clarify and not covered:
        a.label = "clarify"
    elif intent.idk and cov == 0 and len(content_words) <= 18:
        a.label = "idk"
    elif generic_probe:
        # "Why does it work that way?" has no key points: judge the reasoning and whether it stays on the subject.
        a.label = ("strong" if a.reasoning >= 2 and rel >= 0.25 and len(content_words) >= 15 else
                   "partial" if len(content_words) >= 8 and rel >= 0.15 else "weak")
    elif is_project:
        # A project opener has no right answer: judge it by how concrete and personal it is.
        a.label = "strong" if (cov >= 0.5 and a.specificity >= 0.35) or a.specificity >= 0.6 else ("partial" if len(words) >= 8 else "weak")
    elif cov >= 0.6 or (cov >= 0.45 and len(covered) >= 2 and a.reasoning >= 1):
        a.label = "strong"
    elif cov > 0:
        a.label = "partial"
    elif len(content_words) >= 6 and rel < 0.15 and not intent.injection:
        a.label = "offtopic"
    elif intent.injection and len(content_words) < 6:
        a.label = "offtopic"
    else:
        a.label = "weak"
    return a
