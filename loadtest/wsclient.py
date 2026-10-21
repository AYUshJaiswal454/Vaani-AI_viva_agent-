"""Minimal asyncio WebSocket client on top of wsproto (used by the audio bots and end-to-end tests).

Kept tiny on purpose so the load generator has no dependency beyond what the server already needs.
"""
from __future__ import annotations

import asyncio
import json
import ssl
from urllib.parse import urlparse

from wsproto import ConnectionType, WSConnection
from wsproto.events import (AcceptConnection, BytesMessage, CloseConnection, Message, Ping, RejectConnection, Request,
                            TextMessage)


class WSClient:
    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.ws = WSConnection(ConnectionType.CLIENT)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self._reader_task: asyncio.Task | None = None
        self._text_buf: list[str] = []
        self._bytes_buf: list[bytes] = []

    async def connect(self, url: str, headers: dict[str, str] | None = None, timeout: float = 15) -> None:
        u = urlparse(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        ctx = ssl.create_default_context() if u.scheme == "wss" else None
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(u.hostname, port, ssl=ctx), timeout)
        target = u.path + (f"?{u.query}" if u.query else "")
        extra = [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
        self.writer.write(self.ws.send(Request(host=f"{u.hostname}:{port}", target=target, extra_headers=extra)))
        await self.writer.drain()
        while True:
            data = await asyncio.wait_for(self.reader.read(65536), timeout)
            if not data:
                raise ConnectionError("closed during handshake")
            self.ws.receive_data(data)
            for ev in self.ws.events():
                if isinstance(ev, AcceptConnection):
                    self._reader_task = asyncio.create_task(self._read_loop())
                    return
                if isinstance(ev, RejectConnection):
                    raise ConnectionError(f"rejected: {ev.status_code}")

    async def _read_loop(self) -> None:
        try:
            while not self.closed:
                data = await self.reader.read(65536)
                if not data:
                    break
                self.ws.receive_data(data)
                for ev in self.ws.events():
                    if isinstance(ev, TextMessage):
                        self._text_buf.append(ev.data)
                        if ev.message_finished:
                            await self.queue.put("".join(self._text_buf))
                            self._text_buf = []
                    elif isinstance(ev, BytesMessage):
                        self._bytes_buf.append(ev.data)
                        if ev.message_finished:
                            await self.queue.put(b"".join(self._bytes_buf))
                            self._bytes_buf = []
                    elif isinstance(ev, Ping):
                        self.writer.write(self.ws.send(ev.response()))
                    elif isinstance(ev, CloseConnection):
                        try:
                            self.writer.write(self.ws.send(ev.response()))
                        except Exception:
                            pass
                        self.closed = True
                        break
        except Exception:
            pass
        finally:
            self.closed = True
            await self.queue.put(None)

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.writer.write(self.ws.send(Message(data=text)))
        await self.writer.drain()

    async def send_json(self, obj: dict) -> None:
        await self.send_text(json.dumps(obj))

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.writer.write(self.ws.send(Message(data=data)))
        await self.writer.drain()

    async def recv(self, timeout: float | None = None):
        item = await asyncio.wait_for(self.queue.get(), timeout) if timeout else await self.queue.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def close(self) -> None:
        if not self.closed:
            try:
                self.writer.write(self.ws.send(CloseConnection(code=1000)))
                await self.writer.drain()
            except Exception:
                pass
        self.closed = True
        if self.writer:
            self.writer.close()
