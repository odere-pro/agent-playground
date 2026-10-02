"""An error before the handler, answered in the format of the route it was sent to.

Each interface route names its interface in `openapi_extra={INTERFACE_KEY: <name>}`, which also
lists it in the manifest (PoC-3 open note, section 8). `install_validation_errors(app)` adds two
handlers. Each reads the matched route's interface; when an adapter was registered for it with
`register_validation_format(app, adapter)`:

- a `RequestValidationError` (a body FastAPI cannot validate, or JSON it cannot parse) is
  `adapter.error("invalid_body", <message>, False, meta)`: 400 in the format's shape (section 11);
- a 4xx `HTTPException` that FastAPI or Starlette raises before the handler (a body that is not
  UTF-8, a method the route does not allow) keeps its status and headers (`Allow` on a 405), with
  the body of `adapter.error("invalid_body", <detail>, False, meta)`.

Any other route, native `/v1/run` included, keeps FastAPI's answers: 422 and `{"detail": str}`.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request as HTTPRequest
from starlette.responses import Response as HTTPResponse

from chassis.core.inbound import InboundAdapter, Reply, ReplyMeta
from chassis.server.interfaces.ids import resolve_ids
from chassis.server.interfaces.serve import to_response
from chassis.server.pipeline import RunPipeline

__all__ = [
    "INTERFACE_KEY",
    "install_validation_errors",
    "register_validation_format",
    "route_interface",
    "validation_message",
]

INTERFACE_KEY = "x-chassis-interface"
"""The OpenAPI extension on each interface operation; suggested: the name."""

_FORMATS = "validation_formats"


def route_interface(route: object) -> str | None:
    """The `x-chassis-interface` of a matched route, or None."""
    extra = getattr(route, "openapi_extra", None)
    value = extra.get(INTERFACE_KEY) if isinstance(extra, dict) else None
    return value if isinstance(value, str) else None


def validation_message(exc: RequestValidationError) -> str:
    """One line per error: `<location>: <message>`, joined with `"; "`. The input is not echoed."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error.get("loc", ()) if p != "body") or "body"
        parts.append(f"{loc}: {error.get('msg', 'invalid')}")
    return "; ".join(parts) or "invalid body"


def install_validation_errors(app: FastAPI) -> None:
    """Add the handlers once. Done by `mount_interfaces`."""
    if hasattr(app.state, _FORMATS):
        return
    setattr(app.state, _FORMATS, {})

    def format_of(http: HTTPRequest) -> InboundAdapter[Any] | None:
        formats: dict[str, InboundAdapter[Any]] = getattr(app.state, _FORMATS)
        return formats.get(route_interface(http.scope.get("route")) or "")

    def invalid_body(adapter: InboundAdapter[Any], http: HTTPRequest, message: str) -> Reply:
        pipeline: RunPipeline = app.state.pipeline
        meta = ReplyMeta(resolve_ids(http.headers).ids, pipeline.served, int(time.time()))
        return adapter.error("invalid_body", message, False, meta)

    async def on_validation(http: HTTPRequest, exc: Exception) -> HTTPResponse:
        assert isinstance(exc, RequestValidationError)
        adapter = format_of(http)
        if adapter is None:
            return await request_validation_exception_handler(http, exc)
        return to_response(invalid_body(adapter, http, validation_message(exc)))

    async def on_http(http: HTTPRequest, exc: Exception) -> HTTPResponse:
        assert isinstance(exc, HTTPException)
        adapter = format_of(http) if 400 <= exc.status_code < 500 else None
        if adapter is None:
            return await http_exception_handler(http, exc)
        reply = invalid_body(adapter, http, str(exc.detail))
        headers = {**reply.headers, **(exc.headers or {})}
        return to_response(Reply(exc.status_code, headers, reply.body))

    app.add_exception_handler(RequestValidationError, on_validation)
    app.add_exception_handler(HTTPException, on_http)


def register_validation_format(app: FastAPI, adapter: InboundAdapter[Any]) -> None:
    """Answer validation errors on routes marked `adapter.interface` with `adapter.error`."""
    install_validation_errors(app)
    formats: dict[str, InboundAdapter[Any]] = getattr(app.state, _FORMATS)
    formats[adapter.interface] = adapter
