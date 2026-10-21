"""One pooled outbound HTTP client per event loop (LLM, hosted speech APIs)."""
from __future__ import annotations

import asyncio
from typing import Any

try:  # httpx is the standard client; some sandboxes ship a compatible fork named httpx2
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    import httpx2 as httpx  # type: ignore

_client: "httpx.AsyncClient | None" = None
_client_loop = None
_transport_override = None   # tests inject httpx.MockTransport here


def set_transport(transport) -> None:
    global _client, _transport_override
    _transport_override = transport
    _client = None


def client() -> "httpx.AsyncClient":
    global _client, _client_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not _client_loop:
        _client, _client_loop = None, loop
    if _client is None or _client.is_closed:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(30.0, connect=10.0),
            "headers": {"user-agent": "Vaani/1.0 (AI viva examiner)"},
            "limits": httpx.Limits(max_connections=200, max_keepalive_connections=50),
        }
        if _transport_override is not None:
            kwargs["transport"] = _transport_override
        _client = httpx.AsyncClient(**kwargs)
    return _client
