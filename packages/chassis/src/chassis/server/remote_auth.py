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
2. `RequireRun`: exactly one `traceparent` header (more is refused, never guessed: reason
   `traceparent_count`), it must name a run in flight in `state.runs`, and the
   `x-chassis-run-secret` header must hold that run's secret (`ctx.run_secret`, contract v8;
   exactly one header, at most `RUN_SECRET_MAX` bytes, `hmac.compare_digest`). Else 403
   `{"error": {"code": "run_required", ...}}` (the Anthropic body on `/v1/messages`), the same
   bytes for every reason, counted as `RUN_REQUIRED{reason}`. The header is removed before the
   route sees it. So a remote spends tokens and calls tools only inside a run the chassis opened,
   under that run's budget, and only the remote serving that run can. Plain mode
   (`spec.engine.protocol: a2a`) has no secret: traceparent only, counted.

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
RUN_REQUIRED = "chassis.remote.run_required"
"""The 403 counter; `reason` is `traceparent_count`, `no_run`, `secret_missing`, or
`secret_wrong` (suggested)."""
RUN_SECRET_SKIPPED = "chassis.remote.run_secret_skipped"  # pragma: allowlist secret (a name)
"""suggested: a call let in on the traceparent alone, `reason="lane_without_secret"` (plain
mode). A separate counter, so admitted calls never inflate the refusal count."""
RUN_SECRET = "x-chassis-run-secret"  # pragma: allowlist secret (a header name, not a secret)
"""The header that carries `ctx.run_secret` back on every model and MCP call (contract v8)."""
RUN_SECRET_MAX = 128
"""suggested: the longest secret header read, in bytes, checked before the compare."""

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


_DUMMY_SECRET = b"0" * 43  # pragma: allowlist secret (a fixed stand-in, not a secret)
"""The length of a real `ctx.run_secret` (32 random bytes, URL-safe base64)."""


def _dummy_compare(presented: Sequence[bytes]) -> None:
    """One `compare_digest` on the no-run path, as a run in flight would do, so the time does
    not say whether the trace id names a run. The result is never used."""
    value = presented[0] if len(presented) == 1 and len(presented[0]) <= RUN_SECRET_MAX else b""
    hmac.compare_digest(value, _DUMMY_SECRET)


class RequireRun:
    """Pure ASGI middleware: the `traceparent` names a run in `state.runs`, and the
    `x-chassis-run-secret` header holds that run's secret (contract v8, R2-02). Else 403
    `run_required`, the same bytes for every reason, so a caller cannot tell "no such run" from
    "not your run". The header is stripped before the app, so no route can forward it.

    Plain mode (`spec.engine.protocol: a2a`) never sends `ctx` to the remote, so it can never
    send the secret back: its calls are let in on the traceparent alone and counted as
    `RUN_SECRET_SKIPPED`. That is decided by config, never by the caller, and only for a run
    whose record holds no secret.
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
        reason = self._refusal_reason(scope)
        if reason is not None:
            self._counter(RUN_REQUIRED, reason)
            body, headers = _refusal(scope, _RUN_REQUIRED, _RUN_REQUIRED_ANTHROPIC, [])
            await _send_json(send, 403, body, headers)
            return
        name = RUN_SECRET.encode()
        kept = [(k, v) for k, v in scope.get("headers", []) if k.lower() != name]
        await self.app({**scope, "headers": kept}, receive, send)

    def _refusal_reason(self, scope: Scope) -> str | None:
        """None when the call may pass, else the counter's reason. Never logs a header."""
        pairs = scope.get("headers", [])
        name = RUN_SECRET.encode()
        presented = [v for k, v in pairs if k.lower() == name]
        traceparents = [v for k, v in pairs if k.lower() == b"traceparent"]
        if len(traceparents) > 1:
            # Never guessed: this check would read one header and FastMCP's
            # `get_http_headers` the other (its dict keeps the last), so the run key could differ.
            return "traceparent_count"
        header = traceparents[0].decode("latin-1") if traceparents else None
        trace_id = parse_traceparent(header)
        runs = getattr(self._state, "runs", None)
        record = runs.lookup(trace_id) if trace_id is not None and runs is not None else None
        if record is None:
            _dummy_compare(presented)
            return "no_run"
        if self._plain_mode() and record.run_secret is None:
            # Both: a run that holds a secret (opened before a reload to plain mode) needs it.
            self._counter(RUN_SECRET_SKIPPED, "lane_without_secret")
            return None
        if not presented:
            return "secret_missing"
        expected = record.run_secret
        if len(presented) != 1 or len(presented[0]) > RUN_SECRET_MAX or not expected:
            return "secret_wrong"
        if not hmac.compare_digest(presented[0], expected.encode()):
            return "secret_wrong"
        return None

    def _plain_mode(self) -> bool:
        config = getattr(self._state, "config", None)
        return config is not None and config.spec.engine.protocol == "a2a"

    def _counter(self, name: str, reason: str) -> None:
        ports = getattr(self._state, "ports", None)
        if ports is not None:
            ports.telemetry.counter(name, reason=reason)


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
