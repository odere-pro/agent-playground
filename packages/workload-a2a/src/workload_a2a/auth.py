"""Bearer check for the remote lane (`--require-token-env`, `--previous-token-env`).

A pure ASGI middleware: standard library only, no `chassis` import (ADR-002). Every HTTP request,
the agent card included, needs `Authorization: Bearer <token>`. A missing or wrong token gets one
fixed 401 and the app is not called. During a rotation a second, previous token is also accepted
(`--previous-token-env`), so either side can swap first without a 401. After a good check the
`authorization` header is removed from the scope, because a2a-sdk logs request headers at
DEBUG. No token is ever logged or put in an error.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

UNAUTHORIZED_BODY = b'{"error":"unauthorized"}'
"""One body for every refusal, so a caller learns nothing about why."""

_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(UNAUTHORIZED_BODY)).encode()),
    (b"www-authenticate", b"Bearer"),
]


def read_token(env_name: str) -> str:
    """The token in `env_name`. A missing or blank variable raises `ValueError` naming the
    variable, never a value."""
    value = os.environ.get(env_name, "")
    if not value.strip():
        raise ValueError(f"--require-token-env: environment variable {env_name} is unset or empty")
    return value


def read_previous_token(env_name: str) -> str | None:
    """The previous token in `env_name`, or `None` when the variable is unset or blank. That is
    not an error: the same manifest runs before, during, and after a rotation."""
    value = os.environ.get(env_name, "")
    return value if value.strip() else None


class BearerTokenMiddleware:
    """Refuse every HTTP request that lacks `Authorization: Bearer <token>`. When
    `previous_token` is given, that token is accepted too."""

    def __init__(self, app: Any, token: str, previous_token: str | None = None) -> None:
        if not token:
            raise ValueError("token must not be empty")
        if previous_token is not None and not previous_token:
            raise ValueError("previous token must not be empty")
        self._app = app
        self._tokens = [token.encode()]
        if previous_token is not None:
            self._tokens.append(previous_token.encode())

    def __repr__(self) -> str:
        return f"BearerTokenMiddleware(tokens={len(self._tokens)}, values=<hidden>)"

    def _allowed(self, scope: dict[str, Any]) -> bool:
        presented = b""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                scheme, _, rest = bytes(value).partition(b" ")
                if scheme.lower() == b"bearer":
                    presented = rest
                break
        # Always compare against every token, with no early exit, so a missing header costs the
        # same as a wrong one and the time does not say which token matched.
        ok = False
        for token in self._tokens:
            ok |= hmac.compare_digest(presented, token)
        return ok

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        if self._allowed(scope):
            # a2a-sdk logs every request header at DEBUG. Pass the app a copy of the scope
            # without the credential, so no log line downstream can carry it.
            clean = dict(scope)
            clean["headers"] = [
                (k, v) for k, v in scope.get("headers", []) if bytes(k).lower() != b"authorization"
            ]
            await self._app(clean, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 401, "headers": _HEADERS})
        await send({"type": "http.response.body", "body": UNAUTHORIZED_BODY})
