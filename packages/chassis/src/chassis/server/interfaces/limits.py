"""`spec.limits` on the public interfaces: the run budget, the turn count, and the body size.

The interfaces have no auth yet (PoC-8), so the caller sets the run budget. Two checks bound it:

- `enforce_limits(adapter, request, limits, meta)`, called by `serve` right after
  `adapter.to_request`, so one place covers native, OpenAI, Anthropic, and MCP (the MCP tool calls
  `/v1/run`). `budget.max_tokens` above `max_tokens_max`, `budget.timeout_ms` above
  `timeout_ms_max`, or more than `messages_max` turns (`input.data.history` plus the input) raises
  `Refused` with `adapter.error("limit_exceeded", ...)`: 400 in the format's own shape. Refused,
  never clamped: a client that asked for more learns it. The chat adapters also count their raw
  `messages` against `messages_max` in `to_request`, with the same code. A budget below 1 is
  answered before this check on every route today: 422 by `RunRequest` on native (so a tool error
  over MCP), 400 `invalid_body` by the chat adapters. The `value < 1` branch here is a backstop
  for an adapter that lets one through.
- `BodyLimit`, a pure ASGI middleware on the public app (`install_body_limit`): a body over
  `body_bytes_max` is 413 before it is parsed or validated. A `Content-Length` over the cap is
  refused without reading; a body with no length is read up to the cap and refused at the first
  byte over it, else replayed to the app. The 413 body is the route's format: OpenAI's and
  Anthropic's error shapes on their routes (`limit_exceeded`, `invalid_request_error`), `{"detail":
  str}` elsewhere (`/v1/run`, `/v1/mcp`). The MCP tool's in-process call to `/v1/run` passes
  through it too. The cap is read per request from the app's live config.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chassis.core.envelope import Request
from chassis.core.inbound import InboundAdapter, Refused, ReplyMeta
from chassis.server.config import ChassisConfig, LimitsSpec
from chassis.server.interfaces.ids import resolve_ids

__all__ = [
    "LIMIT_EXCEEDED",
    "BodyLimit",
    "body_too_large_message",
    "enforce_limits",
    "install_body_limit",
    "limit_problem",
    "messages_over",
]

LIMIT_EXCEEDED = "limit_exceeded"
"""The error code of every refusal here; 400 by `status_for`, 413 for the body. suggested."""


def messages_over(count: int, limits: LimitsSpec, what: str = "messages") -> str | None:
    """Why `count` turns are too many, or None."""
    if count > limits.messages_max:
        return f"{what}: {count} is more than the limit of {limits.messages_max}"
    return None


def limit_problem(request: Request, limits: LimitsSpec) -> str | None:
    """Why `request` is outside `limits`, or None."""
    for field, ceiling in (
        ("max_tokens", limits.max_tokens_max),
        ("timeout_ms", limits.timeout_ms_max),
    ):
        value = getattr(request.budget, field)
        if value < 1:
            return f"budget.{field}: {value} is below the minimum of 1"
        if value > ceiling:
            return (
                f"budget.{field}: {value} is above this chassis's limit of {ceiling}; "
                f"ask for at most {ceiling}"
            )
    history = request.input.data.get("history")
    if isinstance(history, list):
        return messages_over(len(history) + 1, limits, "input.data.history plus the input")
    return None


def enforce_limits[BodyT](
    adapter: InboundAdapter[BodyT], request: Request, limits: LimitsSpec, meta: ReplyMeta
) -> None:
    """Raise `Refused` with `adapter.error("limit_exceeded", ...)` when `request` is outside
    `limits` (module docstring)."""
    problem = limit_problem(request, limits)
    if problem is not None:
        reply = adapter.error(LIMIT_EXCEEDED, problem, False, meta.for_request(request))
        raise Refused(reply.status, reply.headers, reply.body)


def body_too_large_message(limit: int) -> str:
    return f"the request body is larger than the limit of {limit} bytes"


class BodyLimit:
    """Pure ASGI: 413 for a body over the limit, before the app reads it (module docstring).
    `public` is the FastAPI app, read for the route's format at the time of a refusal and for the
    live limit: `public.state.config.spec.limits.body_bytes_max`, per request, so a config reload
    (`chassis.server.config_loader`) takes effect on the next request. `limit` is the fallback
    when the app has no config.
    """

    def __init__(self, app: ASGIApp, *, limit: int, public: FastAPI) -> None:
        self.app = app
        self.fallback = limit
        self.public = public

    @property
    def limit(self) -> int:
        config: ChassisConfig | None = getattr(self.public.state, "config", None)
        return self.fallback if config is None else config.spec.limits.body_bytes_max

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit
        length = Headers(scope=scope).get("content-length")
        if length is not None:
            if length.isascii() and length.strip().isdigit() and int(length) > limit:
                await self._refuse(scope, receive, send, limit)
                return
            await self.app(scope, receive, send)
            return
        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > limit:
                await self._refuse(scope, receive, send, limit)
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return buffered.pop(0) if buffered else await receive()

        await self.app(scope, replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        message = body_too_large_message(limit)
        adapter = self._format_of(scope["path"])
        if adapter is None:
            response = JSONResponse({"detail": message}, status_code=413)
        else:
            headers = Headers(scope=scope)
            served = self.public.state.pipeline.served
            meta = ReplyMeta(resolve_ids(headers).ids, served, int(time.time()))
            reply = adapter.error(LIMIT_EXCEEDED, message, False, meta)
            response = JSONResponse(reply.body, status_code=413, headers=dict(reply.headers))
        await response(scope, receive, send)

    def _format_of(self, path: str) -> InboundAdapter[Any] | None:
        from chassis.server.interfaces.errors import route_interface  # errors imports serve

        formats: Mapping[str, InboundAdapter[Any]] = getattr(
            self.public.state, "validation_formats", {}
        )
        for route in self.public.routes:
            if isinstance(route, APIRoute) and route.path == path:
                return formats.get(route_interface(route) or "")
        return None


def install_body_limit(app: FastAPI, limits: LimitsSpec) -> None:
    """Add `BodyLimit` to `app` with `limits.body_bytes_max`. Done by `mount_interfaces`."""
    app.add_middleware(BodyLimit, limit=limits.body_bytes_max, public=app)
