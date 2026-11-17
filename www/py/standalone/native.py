"""Network for the device drivers: WebSocket and UDP, in the app and on a normal computer.

In the app (Pyodide): WebSocket is the WebView's own; UDP goes through the app's native plugin (window.EPNative,
see android/EpUdpPlugin.java) - a WebView can't send UDP itself.
On a computer (tools/dev_server.py, tests): asyncio - so the drivers can be tried against the real devices from a PC.
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket
import struct
import sys
from typing import Any
from urllib.parse import urlsplit

IN_APP = sys.platform == "emscripten"


class NetError(Exception):
    pass


# ---------------- UDP ----------------

class _UdpCatcher(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.q: asyncio.Queue[tuple[bytes, Any]] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.q.put_nowait((data, addr))


_udp_lock: asyncio.Lock | None = None


async def udp_request(host: str, port: int, message: str, timeout: float = 3.0, local_port: int | None = None,
                      expect_reply: bool = True) -> str:
    """Send one UDP message and wait for the answer from that host. KEBA answers to port 7090 on the sender, so the
    local port can be fixed (local_port)."""
    if IN_APP:
        try:
            from js import EPNative  # type: ignore
        except ImportError as err:
            raise NetError("UDP kræver Android-appen") from err
        if not getattr(EPNative, "udp", False):
            raise NetError("UDP kræver Android-appen (ikke browseren)")
        try:
            r = await EPNative.udpRequest(host, port, message, int(timeout * 1000), local_port or 0, expect_reply)
        except Exception as err:  # noqa: BLE001 - the plugin rejects with a JS error (timeout, unreachable …)
            raise NetError(str(err)) from err
        if r is None:
            raise NetError("intet svar")
        return str(r)
    loop = asyncio.get_running_loop()
    global _udp_lock
    if _udp_lock is None:
        _udp_lock = asyncio.Lock()
    async with _udp_lock:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", local_port or 0))
        transport, proto = await loop.create_datagram_endpoint(_UdpCatcher, sock=sock)
        try:
            transport.sendto(message.encode(), (host, port))
            if not expect_reply:
                return ""
            end = loop.time() + timeout
            while True:
                left = end - loop.time()
                if left <= 0:
                    raise NetError(f"intet svar fra {host}")
                try:
                    data, addr = await asyncio.wait_for(proto.q.get(), left)
                except asyncio.TimeoutError as err:
                    raise NetError(f"intet svar fra {host}") from err
                if addr[0] == socket.gethostbyname(host):
                    return data.decode(errors="replace")
        finally:
            transport.close()


# ---------------- WebSocket ----------------

class WebSocket:
    """A text WebSocket: connect(), send(), recv(), close()."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[str | None] = asyncio.Queue()
        self.open = False
        self._js: Any = None
        self._proxies: list[Any] = []
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None

    async def connect(self, url: str, timeout: float = 10.0) -> None:
        if IN_APP:
            await self._connect_js(url, timeout)
        else:
            await asyncio.wait_for(self._connect_tcp(url), timeout)
        self.open = True

    # -- in the app: the WebView's WebSocket --
    async def _connect_js(self, url: str, timeout: float) -> None:
        from js import WebSocket as JsWebSocket  # type: ignore
        from pyodide.ffi import create_proxy  # type: ignore

        opened: asyncio.Future = asyncio.get_running_loop().create_future()

        def on_open(_e: Any) -> None:
            if not opened.done():
                opened.set_result(True)

        def on_message(e: Any) -> None:
            self._q.put_nowait(str(e.data))

        def on_close(_e: Any) -> None:
            self.open = False
            self._q.put_nowait(None)
            if not opened.done():
                opened.set_exception(NetError("forbindelsen blev afvist"))

        ws = JsWebSocket.new(url)
        for name, fn in (("onopen", on_open), ("onmessage", on_message), ("onclose", on_close), ("onerror", on_close)):
            p = create_proxy(fn)
            self._proxies.append(p)
            setattr(ws, name, p)
        self._js = ws
        await asyncio.wait_for(opened, timeout)

    # -- on a computer: a small client over asyncio (text frames, ping/pong) --
    async def _connect_tcp(self, url: str) -> None:
        u = urlsplit(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        reader, writer = await asyncio.open_connection(u.hostname, port, ssl=(u.scheme == "wss") or None)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        writer.write((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        await writer.drain()
        status = await reader.readline()
        if b" 101 " not in status:
            raise NetError(f"WebSocket afvist: {status.decode(errors='replace').strip()}")
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        self._reader, self._writer = reader, writer
        self._task = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self) -> None:
        r = self._reader
        buf = b""
        try:
            while True:
                head = await r.readexactly(2)
                fin, op = head[0] & 0x80, head[0] & 0x0F
                n = head[1] & 0x7F
                if n == 126:
                    n = struct.unpack(">H", await r.readexactly(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", await r.readexactly(8))[0]
                mask = await r.readexactly(4) if head[1] & 0x80 else None
                data = await r.readexactly(n)
                if mask:
                    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
                if op == 0x8:
                    break
                if op == 0x9:
                    self._frame(0xA, data)
                    continue
                if op in (0x1, 0x2, 0x0):
                    buf += data
                    if fin:
                        self._q.put_nowait(buf.decode(errors="replace"))
                        buf = b""
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        self.open = False
        self._q.put_nowait(None)

    def _frame(self, op: int, data: bytes) -> None:
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self._writer.write(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    async def send(self, text: str) -> None:
        if not self.open:
            raise NetError("ikke forbundet")
        if IN_APP:
            self._js.send(text)
        else:
            self._frame(0x1, text.encode())
            await self._writer.drain()

    async def recv(self) -> str | None:
        """The next message, or None when the connection is closed."""
        return await self._q.get()

    async def close(self) -> None:
        self.open = False
        try:
            if IN_APP and self._js is not None:
                self._js.close()
            elif self._writer is not None:
                self._frame(0x8, b"")
                self._writer.close()
        except Exception:  # noqa: BLE001
            pass
        for p in self._proxies:
            try:
                p.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._proxies.clear()
