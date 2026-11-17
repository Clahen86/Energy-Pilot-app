"""A small stand-in for aiohttp, so Energy Pilot's core runs unchanged inside the app.

In the app (Pyodide) requests go through the browser's fetch - and in the native app through native HTTP, so
the manufacturers' APIs can be reached without CORS limits. On a normal Python (tests) urllib is used.
Only what Energy Pilot uses is here: ClientSession.get/post with params, headers, json and timeout.
"""

from __future__ import annotations

import asyncio
import enum
import json as _json
import sys
from typing import Any
from urllib.parse import urlencode

from . import web  # noqa: F401  (aiohttp.web)

IN_BROWSER = sys.platform == "emscripten"


class ClientError(Exception):
    pass


class ClientConnectionError(ClientError):
    pass


class ClientResponseError(ClientError):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"{status} {message}".strip())
        self.status = status
        self.message = message


class ClientTimeout:
    def __init__(self, total: float | None = None, **_: Any) -> None:
        self.total = total


class WSMsgType(enum.IntEnum):
    TEXT = 1
    BINARY = 2
    CLOSED = 8


class ClientWebSocketResponse:  # only used as a type hint
    pass


class ClientResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body

    async def text(self, encoding: str = "utf-8") -> str:
        return self._body.decode(encoding, errors="replace")

    async def json(self, content_type: Any = None, **_: Any) -> Any:
        return _json.loads(self._body.decode("utf-8") or "null")

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise ClientResponseError(self.status)

    def release(self) -> None:
        pass


class _RequestContext:
    def __init__(self, coro: Any) -> None:
        self._coro = coro
        self._resp: ClientResponse | None = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> ClientResponse:
        self._resp = await self._coro
        return self._resp

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _timeout_s(timeout: Any) -> float:
    if isinstance(timeout, ClientTimeout):
        return float(timeout.total or 60)
    if isinstance(timeout, (int, float)):
        return float(timeout)
    return 60.0


async def _browser_fetch(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> ClientResponse:
    from pyodide.http import pyfetch  # type: ignore

    kwargs: dict[str, Any] = {"method": method, "headers": headers}
    if body is not None:
        kwargs["body"] = body.decode("utf-8")
    try:
        r = await asyncio.wait_for(pyfetch(url, **kwargs), timeout)
        data = await r.bytes()
    except asyncio.TimeoutError as err:
        raise ClientConnectionError("timeout") from err
    except Exception as err:  # noqa: BLE001 - network refused, CORS, offline
        raise ClientConnectionError(str(err)) from err
    return ClientResponse(r.status, data)


async def _urllib_fetch(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> ClientResponse:
    import urllib.error
    import urllib.request

    def go() -> ClientResponse:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return ClientResponse(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as err:
            return ClientResponse(err.code, err.read() or b"")
        except Exception as err:  # noqa: BLE001
            raise ClientConnectionError(str(err)) from err

    return await asyncio.to_thread(go)


class ClientSession:
    def __init__(self, *_: Any, headers: dict[str, str] | None = None, **__: Any) -> None:
        self._headers = dict(headers or {})
        self.closed = False

    async def __aenter__(self) -> "ClientSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed = True

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                json: Any = None, data: Any = None, timeout: Any = None, **_: Any) -> _RequestContext:
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode({k: v for k, v in params.items() if v is not None})
        h = {**self._headers, **(headers or {})}
        body = None
        if json is not None:
            body = _json.dumps(json).encode()
            h.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = data if isinstance(data, bytes) else str(data).encode()
        fetch = _browser_fetch if IN_BROWSER else _urllib_fetch
        return _RequestContext(fetch(method.upper(), url, h, body, _timeout_s(timeout)))

    def get(self, url: str, **kw: Any) -> _RequestContext:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> _RequestContext:
        return self.request("POST", url, **kw)

    def put(self, url: str, **kw: Any) -> _RequestContext:
        return self.request("PUT", url, **kw)

    def ws_connect(self, *_: Any, **__: Any) -> Any:
        raise ClientConnectionError("websockets are not used in the app")
