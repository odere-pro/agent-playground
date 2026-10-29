"""Caller auth and scopes on the public app (PoC-7, 022 H-6).

`install_security(app)` adds one pure ASGI middleware to the public app. It is not on the proxy
app (the proxy port is localhost-only and serves the workload) nor on the remote listener (it has
its own token check, `server.remote_auth`).

**`CallerAuth`.** Every HTTP request except `GET /health` and `GET /ready` calls
`ports.auth.authenticate(token)` with the bearer token (`Authorization: Bearer <token>`; None when
the header is absent or not a bearer). Then the route's scope from `spec.security`:

- `run_scope`: `/v1/run`, `/v1/chat/completions` (the OpenAI interface, public port),
  `/v1/messages` (the Anthropic interface, public port), and `/v1/mcp`.
- `feedback_scope`: `/v1/feedback`.
- `read_scope`: `/manifest`, `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`.
- Any other path needs a valid token and no scope (it is a 404 anyway).

Only `http` is checked and only `lifespan` passes through unchecked. Every other scope type is
refused, as on the remote listener: a websocket is closed with 1008 before accept, any other type
is dropped, and the app never sees it (review R2-29).

`unauthenticated` is 401 with `WWW-Authenticate: Bearer`; `auth_unavailable` is 503 with
`Retry-After` (suggested: 1 s); a missing scope is 403 `forbidden`, and its `WWW-Authenticate`
names the scope (`error="insufficient_scope"`, RFC 6750). Bodies are the route's format: OpenAI's
`{"error": {...}}` on its route, Anthropic's `{"type": "error", "error": {...}}` on its route,
`{"detail": {"code", "message"}}` elsewhere. Messages are fixed texts (`MESSAGES`); the
port's own message, which can say why a token failed, goes to the log, never the token. Every
refusal is counted as `chassis.auth_refused{code, path_class}` (suggested).

The principal goes on the request's state (`request.state.principal`, `scope["state"]`), so a
route (the feedback route of W3, the run pipeline) reads `principal.subject`.

With `NoAuth` (the default, `spec.adapters.auth: none`) every caller is the anonymous principal
with every scope, so nothing changes for a chassis that names no auth. Before the lifespan has
built the ports, a chassis whose `spec.adapters` (over the profile) names `auth: none` lets the
call through as before; any other answers 503 `auth_unavailable`.

**The MCP hop is not trusted by address.** `/v1/mcp` runs the agent through an in-process call
to `/v1/run` (`adapters/mcp/agent.py`), which passes this middleware again like any other call.
That call carries the MCP caller's own `Authorization` header (the tool's request hook copies it
from the inbound MCP request), so `/v1/run` checks the same token again, and the loopback address
and `x-chassis-interface` grant nothing.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any, Literal

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis.ports.auth import ANY_SCOPE, AuthError, AuthPort, Principal
from chassis.profiles import AdapterNotAvailable, merge_adapters
from chassis.server.config import ChassisConfig

__all__ = [
    "AUTH_REFUSED",
    "OPEN_PATHS",
    "CallerAuth",
    "install_security",
    "path_class",
    "principal_of",
    "refusal_body",
    "send_json",
]

AUTH_REFUSED = "chassis.auth_refused"
RETRY_AFTER_S = "1"

OPEN_PATHS = frozenset({"/health", "/ready"})
RUN_PATHS = frozenset({"/v1/run", "/v1/chat/completions", "/v1/messages", "/v1/mcp"})
FEEDBACK_PATHS = frozenset({"/v1/feedback"})
READ_PATHS = frozenset({"/manifest", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
OPENAI_PATH = "/v1/chat/completions"
ANTHROPIC_PATH = "/v1/messages"

PathClass = Literal["open", "run", "feedback", "read", "other"]

MESSAGES = {
    "unauthenticated": "a valid bearer token is required",
    "auth_unavailable": "the chassis cannot check credentials now; retry later",
    "forbidden": "the token does not grant the scope this route needs",
}


def path_class(path: str) -> PathClass:
    trimmed = path.rstrip("/") or "/"
    if trimmed in OPEN_PATHS:
        return "open"
    if trimmed in RUN_PATHS or trimmed.startswith("/v1/mcp/"):
        return "run"
    if trimmed in FEEDBACK_PATHS:
        return "feedback"
    if trimmed in READ_PATHS:
        return "read"
    return "other"


def _bearer(headers: Headers) -> str | None:
    value = headers.get("authorization")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


_CALLER: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "chassis_caller", default=None
)


def current_user_id() -> str | None:
    """The checked caller's subject for this request, for `user.id` on spans and in baggage
    (PoC-7, ADR-007). None with auth off (`NoAuth`'s anonymous principal) or outside a request.
    """
    principal = _CALLER.get()
    if principal is None or ANY_SCOPE in principal.scopes:
        return None
    return principal.subject


def principal_of(scope: Scope) -> Principal | None:
    """The principal `CallerAuth` put on this request, or None (a route outside the app)."""
    state = scope.get("state")
    if isinstance(state, dict):
        found = state.get("principal")
        return found if isinstance(found, Principal) else None
    found = getattr(state, "principal", None)
    return found if isinstance(found, Principal) else None


_KINDS = {401: "authentication_error", 403: "permission_error", 429: "rate_limit_error"}
"""The error `type` per status in the OpenAI and Anthropic shapes; suggested for 429."""


def refusal_body(path: str, code: str, message: str, status: int) -> dict[str, Any]:
    """A refusal before the route runs, in the shape of the route's format: OpenAI's on its
    route, Anthropic's on its route, `{"detail": {"code", "message"}}` elsewhere. Shared by
    `CallerAuth` and the rate limit (`chassis.server.rate_limit`)."""
    if path == OPENAI_PATH:
        kind = _KINDS.get(status, "server_error")
        return {"error": {"message": message, "type": kind, "param": None, "code": code}}
    if path == ANTHROPIC_PATH:
        kind = _KINDS.get(status, "overloaded_error")
        return {"type": "error", "error": {"type": kind, "message": message}}
    return {"detail": {"code": code, "message": message}}


async def send_json(send: Send, status: int, body: dict[str, Any], headers: dict[str, str]) -> None:
    raw = json.dumps(body, separators=(",", ":")).encode()
    out = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(raw)).encode()),
        *[(k.lower().encode(), v.encode()) for k, v in headers.items()],
    ]
    await send({"type": "http.response.start", "status": status, "headers": out})
    await send({"type": "http.response.body", "body": raw})


class CallerAuth:
    """Pure ASGI: the bearer token checked, the route's scope required (module docstring)."""

    def __init__(self, app: ASGIApp, *, public: FastAPI) -> None:
        self.app = app
        self.public = public

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # as the remote listener (review R2-29): never reach the app
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})  # before accept
            return
        path: str = scope["path"]
        kind = path_class(path)
        if kind == "open":
            await self.app(scope, receive, send)
            return
        ports = getattr(self.public.state, "ports", None)
        auth: AuthPort | None = getattr(ports, "auth", None)
        if auth is None:  # before the lifespan built the ports
            if self._auth_off():
                await self.app(scope, receive, send)
            else:
                await self._refuse(send, path, kind, "auth_unavailable", 503)
            return
        try:
            principal = await auth.authenticate(_bearer(Headers(scope=scope)))
        except AuthError as exc:
            self._log(exc, path)
            status = 503 if exc.code == "auth_unavailable" else 401
            code = exc.code if exc.code in MESSAGES else "unauthenticated"
            await self._refuse(send, path, kind, code, status)
            return
        needed = self._scope_for(kind)
        if needed is not None and not principal.allows(needed):
            await self._refuse(send, path, kind, "forbidden", 403, needed=needed)
            return
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["principal"] = principal
        else:  # pragma: no cover - a server that gives a State object
            state.principal = principal
        token = _CALLER.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _CALLER.reset(token)

    def _auth_off(self) -> bool:
        config: ChassisConfig | None = getattr(self.public.state, "config", None)
        if config is None:
            return False
        try:
            named = merge_adapters(config.profile, config.spec.adapters).auth
        except AdapterNotAvailable:
            return False
        return named in (None, "none")

    def _scope_for(self, kind: PathClass) -> str | None:
        config: ChassisConfig = self.public.state.config
        security = config.spec.security
        return {
            "run": security.run_scope,
            "feedback": security.feedback_scope,
            "read": security.read_scope,
        }.get(kind)

    def _log(self, exc: AuthError, path: str) -> None:
        telemetry = getattr(getattr(self.public.state, "ports", None), "telemetry", None)
        if telemetry is not None:
            telemetry.log("warning", "caller refused", code=exc.code, path=path, detail=exc.message)

    async def _refuse(
        self,
        send: Send,
        path: str,
        kind: PathClass,
        code: str,
        status: int,
        *,
        needed: str | None = None,
    ) -> None:
        telemetry = getattr(getattr(self.public.state, "ports", None), "telemetry", None)
        if telemetry is not None:
            telemetry.counter(AUTH_REFUSED, code=code, path_class=kind)
        headers: dict[str, str] = {}
        if status == 401:
            headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
        elif status == 403:
            headers["WWW-Authenticate"] = f'Bearer error="insufficient_scope", scope="{needed}"'
        elif status == 503:
            headers["Retry-After"] = RETRY_AFTER_S
        await send_json(send, status, refusal_body(path, code, MESSAGES[code], status), headers)


def install_security(app: FastAPI) -> None:
    """Add `CallerAuth` to the public app, outside the body limit, so a refused call is never
    read or run."""
    app.add_middleware(CallerAuth, public=app)
