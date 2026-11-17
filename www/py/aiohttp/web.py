"""A stand-in for aiohttp.web: the panel's API handlers in web.py are called directly by the app."""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Awaitable, Callable

Handler = Callable[["Request"], Awaitable["Response"]]


class Request:
    def __init__(self, method: str, path: str, query: dict[str, str] | None = None, body: bytes | None = None,
                 headers: dict[str, str] | None = None) -> None:
        self.method = method.upper()
        self.path = path
        self.query = dict(query or {})
        self.headers = dict(headers or {})
        self._body = body or b""

    @property
    def can_read_body(self) -> bool:
        return bool(self._body)

    @property
    def body_exists(self) -> bool:
        return bool(self._body)

    async def json(self) -> Any:
        return _json.loads(self._body.decode("utf-8") or "null")

    async def text(self) -> str:
        return self._body.decode("utf-8")

    async def read(self) -> bytes:
        return self._body


class Response:
    def __init__(self, *, body: bytes | str | None = None, text: str | None = None, status: int = 200,
                 content_type: str | None = None, headers: dict[str, str] | None = None) -> None:
        if text is not None:
            body = text
        self.body = body.encode() if isinstance(body, str) else (body or b"")
        self.status = status
        self.content_type = content_type or "text/plain"
        self.headers = dict(headers or {})
        if content_type:
            self.headers.setdefault("Content-Type", content_type)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def json_response(data: Any = None, *, status: int = 200, dumps: Callable[[Any], str] = _json.dumps, **kw: Any) -> Response:
    return Response(text=dumps(data), status=status, content_type="application/json", headers=kw.get("headers"))


class FileResponse(Response):
    def __init__(self, path: str | Path, **kw: Any) -> None:
        p = Path(path)
        try:
            data = p.read_bytes()
            status = 200
        except OSError:
            data, status = b"", 404
        ct = "text/html" if p.suffix == ".html" else "application/javascript" if p.suffix == ".js" else "application/octet-stream"
        super().__init__(body=data, status=status, content_type=ct, headers=kw.get("headers"))


class HTTPException(Exception):
    status = 500


class HTTPNotFound(HTTPException):
    status = 404


class HTTPBadRequest(HTTPException):
    status = 400


class _Router:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Handler] = {}

    def add_get(self, path: str, handler: Handler, **_: Any) -> None:
        self.routes[("GET", path)] = handler

    def add_post(self, path: str, handler: Handler, **_: Any) -> None:
        self.routes[("POST", path)] = handler

    def add_static(self, *_: Any, **__: Any) -> None:
        pass  # the app serves its own files


class Application(dict):
    def __init__(self, **_: Any) -> None:
        super().__init__()
        self.router = _Router()

    async def handle(self, req: Request) -> Response:
        handler = self.router.routes.get((req.method, req.path))
        if handler is None:
            return json_response({"error": "not found", "path": req.path}, status=404)
        try:
            return await handler(req)
        except HTTPException as err:
            return json_response({"error": type(err).__name__}, status=err.status)


class AppRunner:  # main.py's server is not used in the app
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def setup(self) -> None:
        pass


class TCPSite:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def start(self) -> None:
        pass
