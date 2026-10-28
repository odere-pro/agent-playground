"""The remote-lane proxy listener (PoC-5 plan, section 2.3): the model proxy and the tool endpoint
on the pod IP, for a workload in another pod.

`create_remote_proxy_app(public_app, tokens)` builds its own FastAPI app from an explicit list of
routes: the model pass-through (`POST /v1/chat/completions` and the Anthropic proxy route `POST
/v1/messages`, `chassis.server.model_proxy` and `model_proxy_messages`) and the MCP tool
endpoint (`/mcp`, `chassis.server.tool_endpoint`). Nothing else: no `/dapr/*`, no docs,
no OpenAPI document; every other path is 404. It shares `public_app.state` (the ports, `runs`),
like the loopback proxy app, and it has no lifespan of its own. Call it before the public app
starts: the tool endpoint hooks into the public app's lifespan.

Two pure ASGI middlewares wrap it, outermost first. Both pass `lifespan` and refuse every
other non-`http` scope (a websocket is closed with 1008 before accept):

1. `BearerAuth(tokens)`: `Authorization: Bearer <t>` must equal one of `tokens` (the current one
   and, during a rotation, the previous one), compared with `hmac.compare_digest` against every
   token. Missing, not `Bearer`, or wrong: 401 with one fixed body,
   `{"error": {"code": "remote_unauthenticated", ...}}`; on `/v1/messages` the Anthropic body
   `{"type": "error", "error": {...}, "request_id": ...}` (contract v5, A.8), chosen by the raw
   path. `x-api-key` alone is not a credential. Counted as
   `chassis.remote.auth_failed` (`reason`: `missing` or `wrong`, suggested) and logged with the
   method and the path only, never the header. The header is removed before the route sees it.
2. `RequireRun`: the `traceparent` must name a run in flight in `state.runs`. Else 403
   `{"error": {"code": "run_required", ...}}` (the Anthropic body on `/v1/messages`). So a
   remote spends tokens and calls tools only inside a run the chassis opened, under that run's
   budget.

`chassis serve --remote-proxy-host <pod IP>` runs this app on `--remote-proxy-port` (suggested
8091), only in the `remote` lane (`chassis.server.cli`). The loopback proxy app is unchanged.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from chassis import CHASSIS_VERSION
from chassis.adapters.anthropic_compat.model_wire import WireError, error_body, new_ids
from chassis.core.trace import parse_traceparent
from chassis.server.model_proxy import model_proxy_router
from chassis.server.tool_endpoint import mount_tool_endpoint

log = logging.getLogger("chassis.remote_auth")

AUTH_FAILED = "chassis.remote.auth_failed"  # suggested
DEFAULT_REMOTE_PROXY_PORT = 8091  # suggested

_UNAUTHENTICATED = json.dumps(
    {
        "error": {
            "code": "remote_unauthenticated",
            "type": "authentication_error",
            "message": "missing or invalid bearer token",
        }
    }
).encode()
_RUN_REQUIRED = json.dumps(
    {
        "error": {
            "code": "run_required",
            "type": "permission_error",
            "message": "the traceparent names no run in flight",
        }
    }
).encode()

# The Anthropic proxy route's variants (contract v5, A.8): chosen by the raw request path.
ANTHROPIC_PATH = "/v1/messages"
_UNAUTHENTICATED_ANTHROPIC = WireError(
    401, "authentication_error", "remote_unauthenticated", "missing or invalid bearer token", False
)
_RUN_REQUIRED_ANTHROPIC = WireError(
    403, "permission_error", "run_required", "the traceparent names no run in flight", False
)


def _refusal(
    scope: Scope, v4_body: bytes, anthropic: WireError, extra: list[tuple[bytes, bytes]]
) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    """The body and headers of a middleware refusal: Anthropic's shape on `/v1/messages`, v4's
    `{"error": {...}}` on every other path. Status, counters, and order do not change."""
    if scope.get("path") != ANTHROPIC_PATH:
        return v4_body, extra
    _, request_id = new_ids(uuid.uuid4().hex[:24])
    body = json.dumps(error_body(anthropic, request_id)).encode()
    headers = [(b"request-id", request_id.encode()), (b"x-should-retry", b"false"), *extra]
    return body, headers


def remote_tokens(auth: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The tokens the remote listener accepts, from `spec.engine.auth` (its mapping form): the
    current one from `token_env` (required, not empty), then the previous one from
    `previous_token_env` when it is set and not empty. Raises `LookupError` naming the variable,
    never a value.
    """
    if not auth:
        raise LookupError("the remote proxy needs spec.engine.auth (a bearer token variable)")
    name = str(auth.get("token_env") or "")
    current = os.environ.get(name, "") if name else ""
    if not current:
        raise LookupError(f"the remote proxy token variable {name or '(none)'} is not set")
    tokens = [current]
    previous_name = auth.get("previous_token_env")
    if previous_name:
        previous = os.environ.get(str(previous_name), "")
        if previous:
            tokens.append(previous)
    return tuple(tokens)


async def _refuse_non_http(scope: Scope, send: Send) -> None:
    """Every scope type but `http` and `lifespan` is refused on this listener (F4): a websocket
    is closed with 1008 before accept; any other type is dropped, the app never sees it.
    """
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})


async def _send_json(
    send: Send, status: int, body: bytes, extra: list[tuple[bytes, bytes]]
) -> None:
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *extra,
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class BearerAuth:
    """Pure ASGI middleware: a bearer token from `tokens`, or 401 `remote_unauthenticated`."""

    def __init__(self, app: ASGIApp, tokens: Sequence[str], *, state: Any) -> None:
        kept = tuple(t.encode() for t in tokens if t)
        if not kept:
            raise ValueError("BearerAuth needs at least one token that is not empty")
        self.app = app
        self._tokens = kept
        self._state = state

    def __repr__(self) -> str:
        return f"BearerAuth(tokens={len(self._tokens)})"

    def _accepts(self, presented: bytes) -> bool:
        ok = False
        for token in self._tokens:  # every token, so the time does not say which one matched
            ok |= hmac.compare_digest(presented, token)
        return ok

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await _refuse_non_http(scope, send)
            return
        values = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
        reason = "missing"
        if len(values) == 1:
            scheme, _, presented = values[0].partition(b" ")
            if scheme.lower() == b"bearer" and presented.strip():
                if self._accepts(presented.strip()):
                    headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"authorization"]
                    await self.app({**scope, "headers": headers}, receive, send)
                    return
                reason = "wrong"
            else:
                reason = "wrong"
        elif values:
            reason = "wrong"  # more than one header: refused, never guessed
        self._refused(scope, reason)
        body, headers = _refusal(
            scope, _UNAUTHENTICATED, _UNAUTHENTICATED_ANTHROPIC, [(b"www-authenticate", b"Bearer")]
        )
        await _send_json(send, 401, body, headers)

    def _refused(self, scope: Scope, reason: str) -> None:
        method, path = scope.get("method", ""), scope.get("path", "")
        log.warning("remote proxy refused a call: %s (%s %s)", reason, method, path)
        ports = getattr(self._state, "ports", None)
        if ports is not None:
            ports.telemetry.counter(AUTH_FAILED, reason=reason)


class RequireRun:
    """Pure ASGI middleware: the `traceparent` names a run in `state.runs`, or 403
    `run_required`.
    """

    def __init__(self, app: ASGIApp, *, state: Any) -> None:
        self.app = app
        self._state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await _refuse_non_http(scope, send)
            return
        header = next((v for k, v in scope.get("headers", []) if k.lower() == b"traceparent"), None)
        trace_id = parse_traceparent(header.decode("latin-1") if header is not None else None)
        runs = getattr(self._state, "runs", None)
        if trace_id is None or runs is None or runs.lookup(trace_id) is None:
            ports = getattr(self._state, "ports", None)
            if ports is not None:
                ports.telemetry.counter("chassis.remote.run_required")
            body, headers = _refusal(scope, _RUN_REQUIRED, _RUN_REQUIRED_ANTHROPIC, [])
            await _send_json(send, 403, body, headers)
            return
        await self.app(scope, receive, send)


def create_remote_proxy_app(public_app: FastAPI, tokens: Sequence[str]) -> FastAPI:
    """The remote proxy app over the public app's state, wrapped in `BearerAuth` then
    `RequireRun`. Serve it with `lifespan="off"`.
    """
    if not any(tokens):  # Starlette builds middleware on the first request: check now
        raise ValueError("BearerAuth needs at least one token that is not empty")
    remote = FastAPI(
        title="chassis-remote-proxy",
        version=CHASSIS_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    remote.state = public_app.state
    remote.include_router(model_proxy_router(remote))
    mount_tool_endpoint(remote, lifespan_app=public_app)
    # `add_middleware` puts the last one outermost: auth runs first, then the run check.
    remote.add_middleware(RequireRun, state=public_app.state)
    remote.add_middleware(BearerAuth, tokens=tuple(tokens), state=public_app.state)
    return remote
