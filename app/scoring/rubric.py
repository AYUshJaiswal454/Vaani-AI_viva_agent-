"""Rubric: five dimensions, each scored 0-5, weighted to a total out of 100 (weights 30/25/20/15/10 by default)."""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache

from ..config import DATA_FILES

DIMENSIONS = ("conceptual", "depth", "problem_solving", "practical", "communication")


@lru_cache(maxsize=1)
def _default() -> dict:
    return json.loads((DATA_FILES / "rubric_default.json").read_text(encoding="utf-8"))


def default_rubric() -> dict:
    return copy.deepcopy(_default())


def validate_rubric(rubric: dict | None) -> dict:
    """Faculty may change weights and anchor wording; the five dimension ids stay fixed and weights must total 100."""
    if not rubric or not rubric.get("dimensions"):
        return default_rubric()
    base = {d["id"]: d for d in default_rubric()["dimensions"]}
    dims = []
    for d in rubric["dimensions"]:
        if d.get("id") not in base:
            raise ValueError(f"Unknown rubric dimension: {d.get('id')}")
        merged = {**base[d["id"]], **{k: v for k, v in d.items() if v not in (None, "")}}
        merged["weight"] = float(merged["weight"])
        if merged["weight"] < 0:
            raise ValueError("Weights cannot be negative")
        dims.append(merged)
    if {d["id"] for d in dims} != set(DIMENSIONS):
        raise ValueError("The rubric must contain all five dimensions")
    total = sum(d["weight"] for d in dims)
    if abs(total - 100) > 0.01:
        raise ValueError(f"Weights must add up to 100 (they add up to {total:g})")
    out = default_rubric()
    out["dimensions"] = sorted(dims, key=lambda d: DIMENSIONS.index(d["id"]))
    if rubric.get("version"):
        out["version"] = str(rubric["version"])
    return out


def weights(rubric: dict) -> dict[str, float]:
    return {d["id"]: float(d["weight"]) for d in rubric["dimensions"]}


def total_100(scores: dict[str, int | float], rubric: dict) -> float:
    w = weights(rubric)
    return round(sum(max(0.0, min(5.0, float(scores.get(k, 0)))) / 5.0 * w[k] for k in w), 1)


def rubric_hash(rubric: dict) -> str:
    return hashlib.sha256(json.dumps(rubric, sort_keys=True).encode()).hexdigest()[:8]
