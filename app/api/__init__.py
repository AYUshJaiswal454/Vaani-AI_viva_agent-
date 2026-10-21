"""HTTP API: every router in one list."""
from __future__ import annotations


def routes() -> list:
    from . import auth, banks, media, student, system, vivas

    out = []
    for mod in (auth, student, banks, vivas, media, system):
        out.extend(mod.router.routes)
    return out
