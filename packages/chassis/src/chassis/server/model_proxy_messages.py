"""The Anthropic proxy route: `POST /v1/messages` on the model proxy (contract v5, part A).

The Claude Agent SDK's CLI points `ANTHROPIC_BASE_URL` at a chassis proxy listener and calls this
route; the chassis maps the body onto `ModelPort` (`chassis.adapters.anthropic_compat.model_wire`)
and answers in Anthropic's shape. This is not the Anthropic interface on the public port
(`chassis.server.interfaces.anthropic`), which calls the agent.

The route shares `model_proxy.admit` with the OpenAI route: one run lookup, one budget, one
uncorrelated cap, and on the remote listener the same `BearerAuth` and `RequireRun`. No header is
read or forwarded: `authorization`, `x-api-key`, `anthropic-version`, `anthropic-beta`. The query
string is ignored. The `traceparent` is the only header read.

Streaming. The model call starts at once, and the route holds the reply for up to
`FIRST_CHUNK_WAIT_S` for the first chunk or the model's error. An error in that window is an HTTP
error with its real status (the CLI retries on status). After that an error is one `event: error`
frame and no `message_stop`. A `ping` frame goes out when no chunk has come for `PING_INTERVAL_S`;
the wait is one pending read that is never cancelled, so no chunk is lost. Refusals before the
model (400, 401, 403, 429, 503) are plain JSON, also when `stream` is true.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi import Request as HTTPRequest
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from chassis.adapters.anthropic_compat.model_wire import (
    BODY_CAP_BYTES,
    TOO_LARGE,
    ParsedMessages,
    RefusedField,
    StreamEncoder,
    WireError,
    error_body,
    message_json,
    model_error_to_wire,
    new_ids,
    parse_messages_request,
    refusal_error,
)
from chassis.ports.model import ModelChunk, ModelError, Usage
from chassis.ports.telemetry import TelemetryPort
from chassis.server.model_proxy import (
    SPAN,
    Admission,
    Refusal,
    _Hold,
    admit,
    is_ready,
    note_denied,
    note_rejected,
)

FIRST_CHUNK_WAIT_S = 5.0  # suggested
PING_INTERVAL_S = 15.0  # suggested
# suggested: the wall clock of a stream with no run to bound it. A correlated stream is bounded
# by what is left of its run's `budget.timeout_ms`.
UNCORRELATED_STREAM_DEADLINE_S = 300.0
IGNORED = "chassis.model_proxy.ignored"  # suggested
STREAM_TIMEOUT = WireError(
    504, "timeout_error", "model_timeout", "the model route did not answer in time", True
)
NOT_READY = WireError(503, "overloaded_error", "not_ready", "the chassis is not ready", True)


def _note(telemetry: TelemetryPort, route: str, exc: ModelError, err: WireError) -> None:
    if err.code == "model_route_denied":
        note_denied(telemetry, route, exc)
    elif err.code == "model_rejected_request":
        note_rejected(telemetry, route, exc)


def error_response(err: WireError, request_id: str) -> JSONResponse:
    """The Anthropic error body with its status, `request-id`, and `x-should-retry`."""
    return JSONResponse(
        error_body(err, request_id),
        status_code=err.status,
        headers={"request-id": request_id, "x-should-retry": "true" if err.retryable else "false"},
    )


def _refusal_error(refusal: Refusal) -> WireError:
    return WireError(429, "rate_limit_error", refusal.code, refusal.message, refusal.retryable)


async def _next(chunks: AsyncIterator[ModelChunk]) -> ModelChunk | None:
    """One read of the model stream; `None` at its end. Run as a task that is never cancelled
    while the stream is in use."""
    try:
        return await chunks.__anext__()
    except StopAsyncIteration:
        return None


class _MessagesStream(StreamingResponse):
    """The streamed answer. The hold phase runs inside `__call__`, before the response starts, so
    a model error in the window is a real HTTP status and the whole call is one span."""

    def __init__(
        self,
        admission: Admission,
        parsed: ParsedMessages,
        msg_id: str,
        request_id: str,
    ) -> None:
        headers = {"request-id": request_id, "cache-control": "no-cache"}
        super().__init__(_empty(), media_type="text/event-stream", headers=headers)
        self._admission = admission
        self._parsed = parsed
        self._msg_id = msg_id
        self._request_id = request_id
        self._deadline = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        hold = self._admission.hold
        try:
            with self._admission.bundle.telemetry.span(SPAN, **self._admission.attributes):
                self._deadline = time.monotonic() + _deadline_s(hold)
                hold.charge_abandoned = True
                chunks = self._admission.bundle.model.stream(
                    self._parsed.messages,
                    route=self._parsed.route,
                    tools=self._parsed.tools,
                    temperature=self._parsed.temperature,
                    max_tokens=hold.max_tokens,
                )
                # The upstream call starts with the first read. A chassis bug before this line
                # (building the stream) charges nothing.
                hold.started = True
                first = asyncio.ensure_future(_next(chunks))
                try:
                    await asyncio.wait({first}, timeout=FIRST_CHUNK_WAIT_S)
                    failure = self._early_failure(first)
                except BaseException:
                    first.cancel()
                    raise
                if failure is not None:
                    hold.settle(None)
                    await failure(scope, receive, send)
                    return
                self.body_iterator = self._body(chunks, first)
                await super().__call__(scope, receive, send)
        finally:
            # Starlette leaves the body generator unclosed when the client goes: close it, so its
            # `finally` frees the upstream stream now, then give the reservation back.
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
            hold.release()

    def _early_failure(self, first: asyncio.Future[ModelChunk | None]) -> Response | None:
        if not first.done():
            return None
        exc = first.exception()
        if exc is None:
            return None
        if not isinstance(exc, ModelError):
            # A chassis or adapter bug, not the model's answer: nothing is charged.
            self._admission.hold.settle(None)
            raise exc
        err = model_error_to_wire(exc)
        self._note(exc, err)
        return error_response(err, self._request_id)

    def _note_timeout(self) -> None:
        self._admission.bundle.telemetry.counter(
            "chassis.model_stream_timeout", route=self._parsed.route
        )

    def _note(self, exc: ModelError, err: WireError) -> None:
        _note(self._admission.bundle.telemetry, self._parsed.route, exc, err)

    async def _body(
        self, chunks: AsyncIterator[ModelChunk], first: asyncio.Future[ModelChunk | None]
    ) -> AsyncIterator[str]:
        hold = self._admission.hold
        enc = StreamEncoder(self._msg_id, self._parsed.route)
        pending = first
        failed = False
        ended = False
        timed_out = False
        try:
            yield enc.start()
            yield enc.ping()
            while True:
                left = self._deadline - time.monotonic()
                if left <= 0:
                    timed_out = True
                    self._note_timeout()
                    yield enc.error(STREAM_TIMEOUT)
                    break
                done, _ = await asyncio.wait({pending}, timeout=min(PING_INTERVAL_S, left))
                if not done:
                    if self._deadline - time.monotonic() > 0:
                        yield enc.ping()
                    continue
                try:
                    chunk = pending.result()
                except ModelError as exc:
                    err = model_error_to_wire(exc)
                    self._note(exc, err)
                    yield enc.error(err)
                    failed = True
                    break
                if chunk is None:
                    break
                if chunk.usage is not None:
                    hold.seen.append(chunk.usage)
                for frame in enc.chunk(chunk):
                    yield frame
                pending = asyncio.ensure_future(_next(chunks))
            if not (failed or timed_out):
                for frame in enc.finish():
                    yield frame
            ended = True
        finally:
            await _close(pending, chunks)
            if timed_out:
                # The upstream may have produced tokens we never saw: charge the reservation.
                hold.settle(hold.seen[-1] if hold.seen else Usage(output_tokens=hold.tokens))
            elif ended or failed:
                hold.settle(hold.seen[-1] if hold.seen else None)
            # Otherwise the client left: `release` in `__call__` charges the abandoned stream.


def _deadline_s(hold: _Hold) -> float:
    """The stream's wall clock: what is left of the run's `timeout_ms`, else the fixed default."""
    record = hold.record
    if record is None:
        return UNCORRELATED_STREAM_DEADLINE_S
    return max(0.0, record.started_at + record.budget.timeout_ms / 1000 - time.monotonic())


_CLEANUPS: set[asyncio.Future[None]] = set()


async def _stop(
    pending: asyncio.Future[ModelChunk | None], chunks: AsyncIterator[ModelChunk]
) -> None:
    if not pending.done():
        pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
    aclose = getattr(chunks, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()


async def _close(
    pending: asyncio.Future[ModelChunk | None], chunks: AsyncIterator[ModelChunk]
) -> None:
    """Stop the pending read, then close the upstream stream so its connection is freed.

    The work runs as its own task. A client that leaves cancels the caller, and under that
    cancellation every await in the caller raises again, so the close would never finish.
    """
    task = asyncio.ensure_future(_stop(pending, chunks))
    _CLEANUPS.add(task)
    task.add_done_callback(_CLEANUPS.discard)
    await asyncio.shield(task)


async def _empty() -> AsyncIterator[str]:
    return
    yield ""


async def _read_capped(http: HTTPRequest) -> bytes | None:
    """The body, or `None` when it is over `BODY_CAP_BYTES`. The cap is enforced while reading,
    before anything is parsed."""
    declared = http.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > BODY_CAP_BYTES:
        return None
    size = 0
    parts: list[bytes] = []
    async for part in http.stream():
        size += len(part)
        if size > BODY_CAP_BYTES:
            return None
        parts.append(part)
    return b"".join(parts)


COUNT_TOKENS_PATH = "/v1/messages/count_tokens"
NOT_FOUND = WireError(
    404,
    "not_found_error",
    "not_found",
    "this route is not served; the model port has no tokenizer",
    False,
)


async def _http_exception(request: HTTPRequest, exc: Exception) -> Response:
    """FastAPI's own handler, except that the 404 of `count_tokens` is an Anthropic error."""
    if (
        isinstance(exc, StarletteHTTPException)
        and exc.status_code == 404
        and request.url.path == COUNT_TOKENS_PATH
    ):
        _, request_id = new_ids(uuid.uuid4().hex[:24])
        return error_response(NOT_FOUND, request_id)
    assert isinstance(exc, StarletteHTTPException)
    return await http_exception_handler(request, exc)


def add_messages_route(router: APIRouter, app: FastAPI) -> None:
    """Register `POST /v1/messages` on the model proxy router."""
    app.add_exception_handler(StarletteHTTPException, _http_exception)

    @router.post("/v1/messages")
    async def messages(http: HTTPRequest) -> Any:
        hex24 = uuid.uuid4().hex[:24]
        msg_id, request_id = new_ids(hex24)
        if not is_ready(app):
            return error_response(NOT_READY, request_id)
        body = await _read_capped(http)
        if body is None:
            return error_response(TOO_LARGE, request_id)
        try:
            raw = json.loads(body)
        except (ValueError, RecursionError):  # deep nesting raises RecursionError
            invalid = RefusedField("invalid_body", "body", "the body is not valid JSON")
            return error_response(refusal_error(invalid), request_id)
        try:
            parsed = parse_messages_request(raw)
        except RefusedField as refused:
            return error_response(refusal_error(refused), request_id)
        admission = admit(
            app,
            route=parsed.route,
            wanted=parsed.max_tokens,
            traceparent=http.headers.get("traceparent"),
            fmt="anthropic",
        )
        if isinstance(admission, Refusal):
            return error_response(_refusal_error(admission), request_id)
        telemetry = admission.bundle.telemetry
        for param in parsed.ignored:
            telemetry.counter(IGNORED, format="anthropic", param=param)
        if parsed.stream:
            return _MessagesStream(admission, parsed, msg_id, request_id)
        return await _complete(admission, parsed, msg_id, request_id)


async def _complete(
    admission: Admission, parsed: ParsedMessages, msg_id: str, request_id: str
) -> Response:
    hold: _Hold = admission.hold
    telemetry = admission.bundle.telemetry
    with telemetry.span(SPAN, **admission.attributes):
        try:
            result = await admission.bundle.model.complete(
                parsed.messages,
                route=parsed.route,
                tools=parsed.tools,
                temperature=parsed.temperature,
                max_tokens=hold.max_tokens,
            )
        except ModelError as exc:
            hold.settle(None)
            err = model_error_to_wire(exc)
            _note(telemetry, parsed.route, exc, err)
            return error_response(err, request_id)
        except BaseException:
            hold.settle(None)
            raise
        hold.settle(result.usage)
    return JSONResponse(
        message_json(result, parsed.route, msg_id), headers={"request-id": request_id}
    )


__all__ = [
    "FIRST_CHUNK_WAIT_S",
    "NOT_READY",
    "PING_INTERVAL_S",
    "add_messages_route",
    "error_response",
]
