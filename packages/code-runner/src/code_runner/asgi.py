"""The HTTP front both servers share: a body cap, and the dispatcher's optional bearer.

`guard(app, limits=..., token=...)` wraps the FastMCP app before uvicorn serves it:

- `BearerAuth` (dispatcher only, when a token is configured): every path but `/health` needs
  `Authorization: Bearer <token>`, compared with `hmac.compare_digest`. Otherwise 401, before a
  byte of the body is read. The token comes from a file (a mounted Secret), never argv or env.
- `BodyCap`: a declared `Content-Length` over the cap is 413 before the body is read; a body
  with no length is counted as it streams and refused at the cap. So at most the cap is held,
  whatever the client sends. A client that leaves mid-body (`http.disconnect`) gets nothing: the
  partial body never reaches the app. The tool's own byte check stays behind it.
- Scopes: `BearerAuth` passes `lifespan` through (no request in it) and closes a `websocket`
  with 1008 before the app sees it; the servers speak HTTP only.

Both answer with a fixed JSON error: never the request body, never the token.
"""

from __future__ import annotations

import hmac
import re
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from code_runner.runner import DEFAULT_LIMITS, Limits

# suggested: JSON escapes one byte of code to at most six (`\u0001`); the rest is the envelope.
MAX_BODY_FACTOR = 8
MIN_TOKEN_BYTES = 32  # suggested: seed.sh makes 64 hex characters
OPEN_PATHS = frozenset({"/health"})  # probes carry no bearer
BODY_TOO_LARGE = "body_too_large"
UNAUTHORIZED = "unauthorized"
WS_POLICY_VIOLATION = 1008  # RFC 6455; before accept, the server answers the handshake 403
_TOKEN_SHAPE = re.compile(rb"[\x21-\x7e]+")  # one visible-ASCII word


def max_body_bytes(limits: Limits) -> int:
    return MAX_BODY_FACTOR * limits.max_code_bytes


def read_token_file(path: str | Path) -> bytes:
    """The inbound token, read once at start. A bad file stops the start; the message has no
    content from the file."""
    token = Path(path).read_bytes().strip()
    if len(token) < MIN_TOKEN_BYTES or not _TOKEN_SHAPE.fullmatch(token):
        raise ValueError(
            f"the inbound token file must hold one token of at least {MIN_TOKEN_BYTES} "
            "visible ASCII characters"
        )
    return token


async def _refuse(scope: Scope, receive: Receive, send: Send, status: int, error: str) -> None:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    await JSONResponse({"error": error}, status_code=status, headers=headers)(scope, receive, send)


class BodyCap:
    """Refuses a request body over `max_bytes` with 413, holding at most `max_bytes`."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _refuse(scope, receive, send, 413, BODY_TOO_LARGE)
            return
        try:
            body = await self._read(receive)
        except _ClientLeft:
            return  # nobody to answer, and a partial body must not run as if whole
        if body is None:
            await _refuse(scope, receive, send, 413, BODY_TOO_LARGE)
            return
        await self.app(scope, _replay(body, receive), send)

    async def _read(self, receive: Receive) -> bytes | None:
        """The whole body, or None once it passes the cap. Raises `_ClientLeft` on a disconnect
        (or any other message) before the last chunk."""
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise _ClientLeft
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)


class _ClientLeft(Exception):
    """The client sent `http.disconnect` before the body ended."""


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # the server's parser refuses it; the count still applies
    return None


def _replay(body: bytes, receive: Receive) -> Receive:
    """The body as one message, then the client's own messages (a disconnect)."""
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay


class BearerAuth:
    """Every path but `OPEN_PATHS` needs exactly one `Authorization: Bearer <token>`."""

    def __init__(self, app: ASGIApp, token: bytes) -> None:
        if not token:
            raise ValueError("an empty inbound token would admit every caller")
        self.app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind != "http":
            # A websocket (or any scope but http) never reaches the app, bearer or not.
            if kind == "websocket":
                await send({"type": "websocket.close", "code": WS_POLICY_VIOLATION})
            return
        if scope["path"] in OPEN_PATHS or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        await _refuse(scope, receive, send, 401, UNAUTHORIZED)

    def _allowed(self, scope: Scope) -> bool:
        values = [v for n, v in scope.get("headers", []) if n == b"authorization"]
        if len(values) != 1:
            return False
        scheme, _, given = values[0].partition(b" ")
        # compare_digest always runs, so the time does not tell a wrong scheme from a wrong token.
        same = hmac.compare_digest(given.strip(), self._token)
        return same and scheme.lower() == b"bearer"


def guard(app: ASGIApp, *, limits: Limits = DEFAULT_LIMITS, token: bytes | None = None) -> ASGIApp:
    """The app uvicorn serves: the bearer (if any) first, then the body cap, then FastMCP."""
    capped: ASGIApp = BodyCap(app, max_body_bytes(limits))
    return capped if token is None else BearerAuth(capped, token)
