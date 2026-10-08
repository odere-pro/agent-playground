"""The model pass-through: `POST /v1/chat/completions`, OpenAI-compatible, over `ports.model`.

A workload points its model base URL here. The proxy maps the OpenAI body (`messages`, `model` as
the route, `temperature`, `max_tokens`, `tools`, `stream`) to `ModelPort.complete` or `.stream`
and maps the result back to the OpenAI shape: chunks, `usage` on the last chunk, `[DONE]`. It never
forwards an inbound `Authorization` header and adds none of its own; the model adapter holds the
key (ADR-001 hard requirement 1). Every call is counted as `chassis.model_calls` per route.

Messages (`docs/contracts/contract-v0.md`, "Changes decided for v1", item 3). Each OpenAI message
maps to one `ModelMessage`, tool loop included: a list of text parts is joined with `"\n"`
(suggested), an assistant's `tool_calls` become `ToolCallRequest`s with `function.arguments`
parsed to an object, a tool message keeps `tool_call_id`, and `name` is carried on any role. A key
the port does not carry is ignored when its value is null or empty, as SDKs echo `refusal`,
`audio`, `function_call`, and `annotations` back. Anything else the port cannot carry (another
part type, the `developer` or `function` role, a key with a value, arguments that are not a JSON
object) is refused before the model is called and before the call is counted: 400 with
`{"error": {"code": "unsupported_message", "type": "invalid_request_error", "param":
"messages[<i>].<field>", ...}}`. Never a 200 on a changed message. The mapping lives in
`chassis.adapters.openai_compat.messages`, shared with the OpenAI interface (`POST
/v1/chat/completions` on the public port, where `model` is the agent, not a route).

Correlation and budgets (PoC-2). The call's `traceparent` names the inbound request's trace id
(`chassis.server.correlation`). When it names an in-flight run, the call runs in the span
`chassis.model.call` (`request_id`, `trace_id`, `route`) and its usage (complete, or the last
streamed chunk) is charged to that run. The forwarded `max_tokens` is capped at what the run has
left (`budget.max_tokens` less spent and reserved tokens), and is that remainder when the workload
sets none; the call reserves that amount at its start and settles it on its usage, so concurrent
calls of one run are never given the same tokens. `max_tokens` bounds output only: a call can
still overshoot by its prompt's input tokens. A run with nothing left is refused before the model
is called: 429 with `{"error": {"code": "budget_exhausted", ...}}`, `retryable: false` when the
tokens are spent and `true` when in-flight calls only hold them; for `stream: true` one error
frame, then `[DONE]`. A call with no `traceparent`, or one that names no in-flight run, is still
served with its own `max_tokens`: it is counted as `chassis.model_calls_uncorrelated` and logged
at `warning`, so PoC-2 can list the engines whose client drops the header (refusing it is PoC-5
egress work).

The uncorrelated cap (PoC-5, plan section 2.12, H16). Those uncorrelated calls are served, but a
replica spends at most `spec.limits.uncorrelated_tokens_per_minute` (L) of them per fixed one-minute
window (`UncorrelatedCap`; a fixed window, not a sliding one, because it is one counter and one
timestamp). It is a reservation, as for a run:

- Admission. A call's worst case is its `max_tokens`, or `DEFAULT_UNCORRELATED_MAX_TOKENS`
  (suggested) capped at L when it sets none; a `max_tokens` below 1 counts as 1. The worst case is
  forwarded as the call's `max_tokens` and reserved in one step under a lock. The call is admitted
  only if the window's spent plus reserved tokens plus the worst case is at most L. Otherwise: 429
  `budget_exhausted`, `chassis.model_calls_refused`, before the model is called. `retryable` is
  `true` when the call would fit an empty window, and `false` for `0` (which refuses every call) and
  for a call whose worst case alone is more than L.
- Settle. The reservation is replaced by the call's usage, charged to the window in which it
  settles. A reservation still held when the window rolls is carried into the next one.
- No usage. A call that reached the model and ends with no usage (an upstream error, a stream the
  client closes before the last frame, a cancel) is charged its whole reservation. The upstream may
  keep generating after the client leaves, and the reservation is the most it can produce, so
  charging it keeps the bound; charging the tokens streamed so far would not. A call refused, or a
  stream whose body never started, is never sent upstream and is charged nothing.

The bound. Every admission keeps spent plus reserved at most L, and output never exceeds the
forwarded `max_tokens` when the upstream honors it (LiteLLM and the fake model server do). So the
tokens charged in one window are at most L plus the input (prompt) tokens of the calls that settle
in it: N concurrent calls overshoot by their N prompts, never by their output. `max_tokens` cannot
bound the prompt; LiteLLM's key budget is the hard cap for that (B11). After a reload lowers L,
calls already in flight keep their reservations; the bound holds with the larger limit.

The cap is read from `state.config` per call, so a reload applies on the next admission. It lives in
this process only, by design: a limit per replica. The remote listener shares it through
`app.state.uncorrelated_cap`. A correlated call never touches it.

This is the model proxy (`POST /v1/chat/completions` on the proxy port). The router is mounted
on the proxy app (`chassis.server.proxy_app`), a localhost-only listener,
never on the public port (`deploy/CLAUDE.md`). The public port's route of the same path is the
OpenAI interface (`chassis.server.interfaces.openai`): a client calls the agent there, and it never
reaches `ports.model` directly.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi import Request as HTTPRequest
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Receive, Scope, Send

from chassis.adapters.openai_compat.messages import UnsupportedMessage, to_port_message
from chassis.ports.bundle import PortBundle
from chassis.ports.model import (
    ModelChunk,
    ModelError,
    ModelMessage,
    ModelResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
)
from chassis.ports.telemetry import TelemetryPort
from chassis.server.correlation import RunRecord, RunRegistry, parse_traceparent

SPAN = "chassis.model.call"
WINDOW_S = 60.0
# suggested: the worst case reserved, and forwarded, for an uncorrelated call that sets no
# `max_tokens`; never more than the cap itself. The epic gives no value.
DEFAULT_UNCORRELATED_MAX_TOKENS = 1024


class UncorrelatedCap:
    """Uncorrelated tokens spent in the current fixed window of `WINDOW_S` seconds, and reserved by
    the calls in flight (the module docstring has the rule and the bound).

    `clock` returns seconds (monotonic); a test passes its own, so nothing sleeps. Each method holds
    a lock, so a check and its reservation are one step even across threads.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._start = clock()
        self._spent = 0
        self._reserved = 0

    def _roll(self) -> None:
        now = self._clock()
        if now - self._start >= WINDOW_S:
            self._start = now
            self._spent = 0  # reservations in flight carry over

    @property
    def spent(self) -> int:
        with self._lock:
            self._roll()
            return self._spent

    @property
    def reserved(self) -> int:
        with self._lock:
            return self._reserved

    @staticmethod
    def worst_case(wanted: int | None, limit: int) -> int:
        """The tokens a call reserves and is forwarded as `max_tokens`."""
        if wanted is None:
            return max(1, min(DEFAULT_UNCORRELATED_MAX_TOKENS, limit))
        return max(1, wanted)

    def reserve(self, worst: int, limit: int) -> bool:
        """Hold `worst` tokens when they fit in `limit` with what is spent and held; else False."""
        with self._lock:
            self._roll()
            if self._spent + self._reserved + worst > limit:
                return False
            self._reserved += worst
            return True

    def release(self, held: int) -> None:
        """Give back a reservation without charging: the call never reached the model."""
        with self._lock:
            self._reserved = max(0, self._reserved - held)

    def settle(self, held: int, usage: Usage | None) -> None:
        """Replace a reservation by the call's usage, or charge all of it when there is none."""
        with self._lock:
            self._roll()
            self._reserved = max(0, self._reserved - held)
            self._spent += held if usage is None else usage.input_tokens + usage.output_tokens


__all__ = [
    "DEFAULT_UNCORRELATED_MAX_TOKENS",
    "SPAN",
    "WINDOW_S",
    "ChatCompletionRequest",
    "UncorrelatedCap",
    "UnsupportedMessage",
    "model_proxy_router",
    "to_port_message",
]


class ToolFunction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolParam(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = "function"
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    """The OpenAI body, the fields the port takes. Others are accepted and ignored."""

    model_config = ConfigDict(extra="ignore")
    model: str
    messages: list[dict[str, Any]]
    # suggested: 0.0 when the body names none, for repeatable test and eval runs; the epic gives
    # no default, and OpenAI's own is 1.0. Backlog 012 H-3 decides it.
    temperature: float = 0.0
    max_tokens: int | None = None
    tools: list[ToolParam] | None = None
    stream: bool = False

    def port_messages(self) -> list[ModelMessage]:
        """The messages on the port; raises `UnsupportedMessage` on the first it cannot carry."""
        return [to_port_message(i, m) for i, m in enumerate(self.messages)]

    def port_tools(self) -> list[ToolSpec] | None:
        if not self.tools:
            return None
        return [
            ToolSpec(
                name=t.function.name,
                description=t.function.description,
                parameters=t.function.parameters,
            )
            for t in self.tools
        ]


def _usage(usage: Usage | None) -> dict[str, int]:
    u = usage or Usage()
    return {
        "prompt_tokens": u.input_tokens,
        "completion_tokens": u.output_tokens,
        "total_tokens": u.input_tokens + u.output_tokens,
    }


def _tool_call(call: ToolCallRequest, index: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }
    if index is not None:
        out = {"index": index, **out}
    return out


def _error_body(exc: ModelError) -> dict[str, Any]:
    return {
        "error": {
            "message": exc.message,
            "type": "model_error",
            "code": exc.code,
            "retryable": exc.retryable,
        }
    }


def _budget_error(record: RunRecord) -> dict[str, Any]:
    held = record.reserved_tokens
    message = (
        f"request {record.request_id} has spent {record.spent_tokens} of its "
        f"{record.budget.max_tokens} tokens"
    )
    if held:
        message += f"; {held} more are held by its in-flight model calls"
    return {
        "error": {
            "message": message,
            "type": "budget_exhausted",
            "code": "budget_exhausted",
            # Retryable only when in-flight calls hold the rest: they may settle for less.
            "retryable": not record.exhausted,
        }
    }


def _uncorrelated_error(limit: int, worst: int) -> dict[str, Any]:
    message = f"uncorrelated model calls are capped at {limit} tokens per minute"
    if 0 < limit < worst:
        message += f"; this call asks for {worst}"
    return {
        "error": {
            "message": message,
            "type": "budget_exhausted",
            "code": "budget_exhausted",
            # Retryable only when the call would fit an empty window.
            "retryable": worst <= limit,
        }
    }


async def _refused_stream(body: dict[str, Any]) -> AsyncIterator[str]:
    yield f"data: {json.dumps(body)}\n\n"
    yield "data: [DONE]\n\n"


def _span_attributes(route: str, trace_id: str | None, record: RunRecord | None) -> dict[str, Any]:
    if record is None:
        return {"route": route, "trace_id": trace_id, "correlated": False}
    return {
        "route": route,
        "trace_id": record.trace_id,
        "request_id": record.request_id,
        "agent": record.agent,
        "correlated": True,
    }


def _completion(
    result: ModelResult, route: str, completion_id: str, created: int
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": result.text or None}
    if result.tool_calls:
        message["tool_calls"] = [_tool_call(c) for c in result.tool_calls]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": result.model or route,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if result.tool_calls else "stop",
            }
        ],
        "usage": _usage(result.usage),
    }


class _Hold:
    """One call's reservation, on its run or on the replica's `UncorrelatedCap`, given back exactly
    once. `settle` charges the usage. `release` runs when a stream response ends: if the body never
    started, the model was never called and nothing is charged; an uncorrelated stream that started
    is charged its last usage seen, or its whole reservation (the module docstring says why).
    """

    def __init__(
        self,
        record: RunRecord | None,
        wanted: int | None,
        cap: UncorrelatedCap | None = None,
        held: int = 0,
    ) -> None:
        self.record = record
        self.cap = cap if record is None else None
        if record is not None:
            self.tokens = record.reserve(wanted)
        else:
            self.tokens = held  # the router reserved it on the cap
        self.max_tokens = self.tokens if record is not None or cap is not None else wanted
        self.started = False
        self.seen: list[Usage] = []
        self._done = False

    def settle(self, usage: Usage | None) -> None:
        if self._done:
            return
        self._done = True
        if self.record is not None:
            self.record.settle(self.tokens, usage)
        elif self.cap is not None:
            self.cap.settle(self.tokens, usage)

    def release(self) -> None:
        if self._done:
            return
        if self.cap is not None and self.started:
            self.settle(self.seen[-1] if self.seen else None)
            return
        self._done = True
        if self.record is not None:
            self.record.release(self.tokens)
        elif self.cap is not None:
            self.cap.release(self.tokens)


async def _sse(
    chunks: AsyncIterator[ModelChunk],
    route: str,
    completion_id: str,
    created: int,
    telemetry: TelemetryPort,
    attributes: dict[str, Any],
    hold: _Hold,
) -> AsyncIterator[str]:
    """The stream inside its span; the run is charged with the usage seen, even on an error."""
    hold.started = True  # from here the model may be called
    with telemetry.span(SPAN, **attributes):
        try:
            async for frame in _frames(chunks, route, completion_id, created, hold.seen):
                yield frame
        finally:
            hold.settle(hold.seen[-1] if hold.seen else None)


class _HeldStream(StreamingResponse):
    """Ends the call's reservation when the response ends before `_sse` settled it: the client
    left before the body started, or while it streamed (Starlette cancels the send and leaves the
    body generator unclosed, so `_sse`'s own `finally` runs late or never).
    """

    def __init__(self, body: AsyncIterator[str], hold: _Hold) -> None:
        super().__init__(body, media_type="text/event-stream")
        self._hold = hold

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._hold.release()


async def _frames(
    chunks: AsyncIterator[ModelChunk],
    route: str,
    completion_id: str,
    created: int,
    seen_usage: list[Usage],
) -> AsyncIterator[str]:
    def frame(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": route,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }
        return f"data: {json.dumps(payload)}\n\n"

    tool_index = 0
    usage: Usage | None = None
    try:
        yield frame({"role": "assistant", "content": ""})
        async for chunk in chunks:
            if chunk.text:
                yield frame({"content": chunk.text})
            if chunk.tool_call is not None:
                yield frame({"tool_calls": [_tool_call(chunk.tool_call, tool_index)]})
                tool_index += 1
            if chunk.usage is not None:
                usage = chunk.usage
                seen_usage.append(usage)
        yield frame({}, "tool_calls" if tool_index else "stop", usage=_usage(usage))
    except ModelError as exc:
        yield f"data: {json.dumps(_error_body(exc))}\n\n"
    yield "data: [DONE]\n\n"


def model_proxy_router(app: FastAPI, *, clock: Callable[[], float] | None = None) -> APIRouter:
    """The router; `app.state.ports` is read per request, so it works after the lifespan.

    `clock` feeds the `UncorrelatedCap` the router makes; the cap is also at
    `app.state.uncorrelated_cap`, which a test may replace.
    """
    router = APIRouter()
    if clock is not None or getattr(app.state, "uncorrelated_cap", None) is None:
        app.state.uncorrelated_cap = UncorrelatedCap(clock or time.monotonic)

    @router.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest, http: HTTPRequest) -> Any:
        if not getattr(app.state, "ready", False):
            return JSONResponse({"detail": "model proxy not ready"}, status_code=503)
        try:
            messages: Sequence[ModelMessage] = body.port_messages()
        except UnsupportedMessage as exc:
            return JSONResponse(exc.body(), status_code=400)
        bundle: PortBundle = app.state.ports
        telemetry = bundle.telemetry
        route = body.model
        telemetry.counter("chassis.model_calls", route=route)
        trace_id = parse_traceparent(http.headers.get("traceparent"))
        runs: RunRegistry = app.state.runs
        record = runs.lookup(trace_id) if trace_id is not None else None
        cap: UncorrelatedCap = app.state.uncorrelated_cap
        worst = 0
        if record is None:
            telemetry.counter("chassis.model_calls_uncorrelated", route=route)
            telemetry.log(
                "warning",
                "model call names no in-flight run; served uncorrelated",
                route=route,
                trace_id=trace_id,
                reason="no traceparent" if trace_id is None else "no in-flight run",
            )
            limit = app.state.config.spec.limits.uncorrelated_tokens_per_minute
            worst = cap.worst_case(body.max_tokens, limit)
            if not cap.reserve(worst, limit):
                telemetry.counter("chassis.model_calls_refused", route=route)
                refusal = _uncorrelated_error(limit, worst)
                if body.stream:
                    return StreamingResponse(
                        _refused_stream(refusal), status_code=429, media_type="text/event-stream"
                    )
                return JSONResponse(refusal, status_code=429)
        elif record.remaining_tokens == 0:
            telemetry.counter("chassis.model_calls_refused", route=route)
            if body.stream:
                return StreamingResponse(
                    _refused_stream(_budget_error(record)),
                    status_code=429,
                    media_type="text/event-stream",
                )
            return JSONResponse(_budget_error(record), status_code=429)
        # No await between the check above and the reservation: one step on the event loop.
        # An uncorrelated call's reservation is already on the cap.
        hold = _Hold(record, body.max_tokens, cap, worst)
        attributes = _span_attributes(route, trace_id, record)
        tools = body.port_tools()
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        if body.stream:
            chunks = bundle.model.stream(
                messages,
                route=route,
                tools=tools,
                temperature=body.temperature,
                max_tokens=hold.max_tokens,
            )
            return _HeldStream(
                _sse(chunks, route, completion_id, created, telemetry, attributes, hold), hold
            )
        with telemetry.span(SPAN, **attributes):
            try:
                result = await bundle.model.complete(
                    messages,
                    route=route,
                    tools=tools,
                    temperature=body.temperature,
                    max_tokens=hold.max_tokens,
                )
            except ModelError as exc:
                hold.settle(None)
                return JSONResponse(_error_body(exc), status_code=502 if exc.retryable else 500)
            except BaseException:
                hold.settle(None)
                raise
            hold.settle(result.usage)
        return JSONResponse(_completion(result, route, completion_id, created))

    return router
