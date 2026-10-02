"""The inbound adapter seam: one wire format (native, OpenAI, Anthropic, MCP) onto the canonical
`Request`, and the run's answer back into that format. No network, no product SDK, no web framework.

An adapter has two pure halves (PoC-3 open note, section 1). Neither does I/O, reads a clock, or
mints an id: the router resolves the ids (`chassis.server.interfaces.ids`) and builds the
`ReplyMeta`, so the same body and the same ids always give the same `Request` and the same reply.

- **In:** `to_request(body, headers, *, ids, served) -> Request`, or `Refused` with the reply the
  client gets (a 400 in the format's own shape, or a 404 for a `model` that is not the agent).
- **Back:** `complete(response, meta) -> Reply` for a run that ended without an `error` event,
  `stream(events, meta) -> StreamReply` for a streamed run, and `error(code, message, retryable,
  meta) -> Reply` for every error answered as HTTP. `status_for(code, retryable)` is the status
  table (section 3) every format shares.

**The answer text** is one rule for every chat format: `answer_text(output)` (`output.text` when
it is a string, else the output as compact JSON with sorted keys) and `rest_of_answer(output,
streamed)`, what a stream still sends after its deltas.

The router owns the run (`chassis.server.interfaces.serve`): it calls `error` for `not_ready`
before the request is mapped, for an `error` event in complete mode, and for an `error` event
before the first `delta` in stream mode (the hold rule), when the format answers errors as HTTP.
Native and MCP do not: their run errors are a 200 envelope with `status: error`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from chassis.core.collector import collect
from chassis.core.envelope import Request, Response, Versions
from chassis.core.events import Event

__all__ = [
    "INVALID_BODY_CODES",
    "PUBLIC_MESSAGES",
    "RUN_FAILED",
    "Ids",
    "InboundAdapter",
    "Interface",
    "Refused",
    "Reply",
    "ReplyMeta",
    "Served",
    "StreamReply",
    "answer_text",
    "public_message",
    "rest_of_answer",
    "status_for",
]

Interface = Literal["native", "openai", "anthropic", "mcp"]
"""The public interfaces: the label on `chassis.requests`, `chassis.run`, and the re-mint
counter."""

INVALID_BODY_CODES = frozenset(
    {"unsupported_parameter", "unsupported_message", "invalid_body", "limit_exceeded"}
)
"""Codes for a refused body or a failed validation: 400 in every format. `limit_exceeded`: a
budget, or a count of messages, outside `spec.limits` (suggested: the code)."""


@dataclass(frozen=True, slots=True)
class Ids:
    """The three ids of one run, already resolved: from the body, a header, or minted."""

    request_id: str
    trace_id: str
    idempotency_key: str

    @classmethod
    def of(cls, request: Request) -> Ids:
        return cls(request.request_id, request.trace_id, request.idempotency_key)


@dataclass(frozen=True, slots=True)
class Served:
    """What this chassis serves: the agent a body must name, and the versions every response
    reports. The router builds it from the config (`RunPipeline.served`).
    """

    agent: str
    agent_version: str
    versions: Versions


@dataclass(frozen=True, slots=True)
class Reply:
    """One HTTP answer, before any web framework sees it. `body` is a JSON object."""

    status: int
    headers: Mapping[str, str]
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StreamReply:
    """A streamed 200 answer: the frames as text, and the headers that go out before the first
    frame (so nothing that is known only at the end, such as the run's status).

    `frames` is iterated once. The router closes it, and the events under it, when the client
    leaves.
    """

    frames: AsyncIterator[str]
    headers: Mapping[str, str] = field(default_factory=dict)
    media_type: str = "text/event-stream"


class Refused(Exception):
    """`to_request` will not map this body. Carries the whole reply: the status, the headers
    (`x-should-retry: false` for the SDK formats), and the body in the format's own shape.
    """

    def __init__(self, status: int, headers: Mapping[str, str], body: dict[str, Any]) -> None:
        super().__init__(f"{status}: {body}")
        self.status = status
        self.headers = dict(headers)
        self.body = body

    @property
    def reply(self) -> Reply:
        return Reply(self.status, self.headers, self.body)


@dataclass(frozen=True, slots=True)
class ReplyMeta:
    """What a back-mapping needs besides the run's own events or response.

    `ids` are the run's ids; after a re-mint they are the new ones. `created` is the Unix time in
    seconds the router took when the run opened (OpenAI's `created`). `request` is the canonical
    request; it is None only for an `error` answered before `to_request` (`not_ready`, a failed
    validation), and always set for `complete` and `stream`. `options` are format-specific reply
    options the router read from the body (for example OpenAI `stream_options.include_usage`);
    only the adapter that defines a key reads it.
    """

    ids: Ids
    served: Served
    created: int
    request: Request | None = None
    options: Mapping[str, object] = field(default_factory=dict)

    def for_request(self, request: Request) -> ReplyMeta:
        """A copy bound to `request`, with its ids."""
        return replace(self, ids=Ids.of(request), request=request)

    async def collect(self, events: Iterable[Event]) -> Response:
        """The `Response` the native envelope gives for these events (`chassis.core.collect`), so
        every format reports the same usage, text, and status. Raises `ValueError` before
        `for_request`.
        """
        if self.request is None:
            raise ValueError("ReplyMeta.collect needs the request; call for_request first")

        async def replay() -> AsyncIterator[Event]:
            for event in events:
                yield event

        return await collect(replay(), self.request, self.served.versions)


@runtime_checkable
class InboundAdapter[BodyT](Protocol):
    """One wire format onto the canonical `Request` and back. Pure: no I/O, no clock, no ids.

    `BodyT` is the body type the router validated: `RunRequest` for native, the SDK's
    `CompletionCreateParams` or `MessageCreateParams` TypedDict for OpenAI and Anthropic.
    `headers` are the inbound HTTP headers; read them with lower-case names (Starlette's `Headers`
    is case-insensitive, a plain dict in a test must use lower-case keys).
    """

    @property
    def interface(self) -> Interface:
        """The interface this adapter serves; the telemetry label."""
        ...

    def to_request(
        self, body: BodyT, headers: Mapping[str, str], *, ids: Ids, served: Served
    ) -> Request:
        """The canonical request with exactly `ids` and `served.agent`/`agent_version`; never ids
        read from the body or headers. Raises `Refused` for a body the format cannot carry.
        """
        ...

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        """The complete answer for a finished run. For OpenAI and Anthropic the router calls it
        only when the run had no `error` event; native gets every run here.
        """
        ...

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        """The streamed answer over the run's events, from the first one. Under the hold rule the
        first `delta` or `end` has already been seen by the router, and is not an `error`; an
        `error` after it is the format's mid-stream error frame. Usage comes from
        `meta.collect(seen)`. Called synchronously; the frames are produced lazily.
        """
        ...

    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        """An HTTP error in the format's shape, with `status_for(code, retryable)` unless the
        format says otherwise, and `x-should-retry` from `retryable` for the SDK formats.
        """
        ...


def status_for(code: str, retryable: bool) -> int:
    """The HTTP status for an error code (PoC-3 open note, section 3; suggested: the table).

    400 for a refused or invalid body, 404 `model_not_found`, 503 `not_ready`, 504
    `a2a.timeout`, 500 `engine_error` and `internal_error`; any other code is 503 when
    retryable, else 502 (the workload failed, not the chassis).

    Idempotency (PoC-4, 018 H-18; suggested: the statuses): 422 `idempotency_conflict` (the key
    was used with another input), 409 `idempotency_in_progress` (a run with the key is still in
    flight), 503 `state_unavailable` (the state store failed; a keyed call fails closed).
    """
    if code in INVALID_BODY_CODES:
        return 400
    if code == "model_not_found":
        return 404
    if code == "idempotency_conflict":
        return 422
    if code == "idempotency_in_progress":
        return 409
    if code in ("not_ready", "state_unavailable"):
        return 503
    if code == "a2a.timeout":
        return 504
    if code in ("engine_error", "internal_error"):
        return 500
    return 503 if retryable else 502


PUBLIC_MESSAGES: Mapping[str, str] = {
    "engine_error": "the agent failed while running",
    "internal_error": "the chassis failed while answering; the run is over",
    "a2a.timeout": "the agent did not answer in time",
    "a2a.transport": "the agent could not be reached",
    "idempotency_conflict": "this Idempotency-Key was already used with a different request",
    "idempotency_in_progress": "a request with this Idempotency-Key is still running; retry later",
    "state_unavailable": "the chassis cannot reach its state store; retry later",
    # Tool codes (PoC-5, plan section 2.6): the text of an MCP tool error on `/mcp`.
    "unknown_tool": "no such tool",
    "bad_arguments": "the tool arguments do not match the tool's input schema",
    "idempotency_key_required": "this is a write tool; call it inside a run",
    "tool_denied": "this tool is not allowed for this service",
    "tool_unavailable": "the tool could not be reached; retry later",
}
"""suggested: the fixed text the OpenAI and Anthropic interfaces send for a run error. An exception
or a connector message can carry internal URLs or an upstream body, so it stays in the log and the
`chassis.run` span; the code itself is still sent."""

RUN_FAILED = "the agent run failed"
"""suggested: the fixed text for any other run error code (a workload's own code)."""


def public_message(code: str) -> str:
    """The fixed client-facing text for a run error `code` (`PUBLIC_MESSAGES`, else
    `RUN_FAILED`)."""
    return PUBLIC_MESSAGES.get(code, RUN_FAILED)


def answer_text(output: Mapping[str, Any] | None) -> str:
    """The answer a chat format shows (PoC-3 open note, section 3): `output.text` when it is a
    string, else the whole output as compact JSON with sorted keys; `""` for no output.
    """
    if output is None:
        return ""
    text = output.get("text")
    if isinstance(text, str):
        return text
    return json.dumps(output, separators=(",", ":"), sort_keys=True)


def rest_of_answer(output: Mapping[str, Any] | None, streamed: str) -> str:
    """What a stream sends after its deltas, as one more chunk: the rest of `answer_text(output)`
    when the streamed text is a prefix of it, else nothing.

    Sent text stands. When the deltas are not a prefix of the answer (the workload ended with
    another text than it streamed, or with structured output after streaming text), nothing more
    is sent: repeating the answer after them would show both. The streamed text is then the
    deltas', and only complete mode shows the answer; the same rule keeps streamed text out of an
    error. With no deltas, the whole answer is the rest.
    """
    answer = answer_text(output)
    return answer[len(streamed) :] if answer.startswith(streamed) else ""
