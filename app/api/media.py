"""Audio: examiner speech clips, the viva recording (decrypted on the fly, seekable) and its waveform."""
from __future__ import annotations

import json
import re
import time
from collections import OrderedDict

from starlette.responses import JSONResponse, Response

from ..config import get_settings
from ..db import audit, get_db
from ..security import verify_signed_token
from ..voice.audio import read_secure
from ..web import ApiError, Ctx, Router, not_found

router = Router("/api")
_audio_cache: "OrderedDict[str, bytes]" = OrderedDict()
_opened: dict[tuple, float] = {}


@router.get("/tts/{key}", auth="user")
def tts_clip(ctx: Ctx):
    key = ctx.path["key"]
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise not_found("Clip")
    row = get_db().fetchone("SELECT path, mime FROM tts_cache WHERE key = ?", (key,))
    if not row:
        raise not_found("Clip")
    path = get_settings().tts_dir / row["path"]
    if not path.exists():
        raise not_found("Clip")
    return Response(path.read_bytes(), media_type=row["mime"], headers={"cache-control": "private, max-age=86400, immutable"})


def _can_listen(ctx: Ctx, session_id: int) -> bool:
    token = ctx.query.get("t")
    if token and verify_signed_token(token) == f"audio:{session_id}":
        return True
    if ctx.user and ctx.user["role"] in ("faculty", "admin"):
        s = get_db().fetchone("SELECT v.owner_id FROM sessions s JOIN vivas v ON v.id = s.viva_id WHERE s.id = ?", (session_id,))
        return bool(s) and (s["owner_id"] == ctx.user["id"] or ctx.user["role"] == "admin")
    return False


def _load(session_id: int, name: str) -> bytes:
    key = f"{session_id}:{name}"
    if key in _audio_cache:
        _audio_cache.move_to_end(key)
        return _audio_cache[key]
    path = get_settings().sessions_dir / str(session_id) / name
    if not path.exists():
        raise not_found("Recording")
    data = read_secure(path)
    _audio_cache[key] = data
    while sum(len(v) for v in _audio_cache.values()) > 200 * 1024 * 1024:
        _audio_cache.popitem(last=False)
    return data


@router.get("/sessions/{session_id:int}/audio", auth="public")
def session_audio(ctx: Ctx):
    sid = int(ctx.path["session_id"])
    if not _can_listen(ctx, sid):
        raise ApiError(403, "Not allowed", "forbidden")
    track = "student.ogg" if ctx.query.get("track") == "student" else "conversation.ogg"
    data = _load(sid, track)
    rng = (ctx.request.headers.get("range") or "").strip()
    if not rng or rng == "bytes=0-":
        # who opened the recording, for the audit trail (once per person, attempt and track every 15 minutes, since a
        # browser fetches the file again on every seek and page view)
        key = ((ctx.user or {}).get("id"), sid, track)
        if time.time() - _opened.get(key, 0) > 900:
            _opened[key] = time.time()
            audit(ctx.user, "audio_opened", "session", sid, data={"track": track})
    if not rng:
        return Response(data, media_type="audio/ogg", headers={"accept-ranges": "bytes", "content-length": str(len(data)),
                                                               "cache-control": "private, no-store"})
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    if not m:
        return Response(status_code=416)
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(data) - 1
    else:
        start, end = max(0, len(data) - int(m.group(2) or 0)), len(data) - 1
    if start >= len(data):
        return Response(status_code=416, headers={"content-range": f"bytes */{len(data)}"})
    return _range(data, start, min(end, len(data) - 1))


def _range(data: bytes, start: int, end: int) -> Response:
    return Response(data[start:end + 1], status_code=206, media_type="audio/ogg",
                    headers={"accept-ranges": "bytes", "content-range": f"bytes {start}-{end}/{len(data)}",
                             "content-length": str(end - start + 1), "cache-control": "private, no-store"})


@router.get("/sessions/{session_id:int}/waveform", auth="public")
def waveform(ctx: Ctx):
    sid = int(ctx.path["session_id"])
    if not _can_listen(ctx, sid):
        raise ApiError(403, "Not allowed", "forbidden")
    path = get_settings().sessions_dir / str(sid) / "waveform.json"
    if not path.exists():
        raise not_found("Waveform")
    return JSONResponse(json.loads(path.read_text()))
