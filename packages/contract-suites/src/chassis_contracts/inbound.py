"""InboundAdapterContract: the same logical request through every inbound adapter gives the same
canonical `Request`, and the run's answer maps back into the format correctly (PoC-3 open note,
sections 2, 3, 6, and 9).

Pure: no app, no engine, no socket. The suite calls the adapter's two halves directly
(`chassis.core.inbound.InboundAdapter`): `to_request`, then `complete`, `stream`, or `error` over
events it scripts itself. What the router owns (`chassis.server.interfaces.serve`) is not tested
here: resolving the ids, the hold rule's peek, re-minting, the `inbound_ignored` counter. The
suite tests the adapter's side of each: it maps exactly the ids it is given, and an `error` the
router hands it (before the first `delta`, or in complete mode) is an HTTP error in the format's
shape.

**The logical cases are written once, in `inbound_cases.py`.** A case is the tuple `(text, system,
history, max_tokens, stream)` (`Logical`) and the canonical `Request` it must give, with the ids
masked. Every binding is compared with the same expectation, so "the same canonical request across
adapters" holds by construction, not by comparing adapters with each other. Every name in
`__all__` here is defined there, except `InboundAdapterContract`.

A binder provides:

- `adapter`: the `InboundAdapter` under test;
- `encode(logical) -> (body, headers)`: the logical request written natively in the format, as
  the router would hand it to `to_request` (for the SDK formats, the validated TypedDict, so a
  lazy `messages` iterator is exercised). Each call gives a fresh body;
- `read_complete(status, headers, body)` and `read_stream(status, headers, frames)`: what a
  client of the format reads back, as a `Readback`. Write them with the official SDK types and
  assert the format's own shape in them (ordering of frames, the terminator);
- `refused_cases` and `ignored_cases`: format-specific `BodyCase`s built on `BASE`.

Declared class attributes switch off what does not apply to a format; each such test skips with
the reason, never silently.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, ClassVar, Literal

import pytest
from chassis.core.envelope import Request
from chassis.core.events import Delta, End, Event, Metrics, Start, ToolCall
from chassis.core.inbound import (
    INVALID_BODY_CODES,
    Ids,
    InboundAdapter,
    Refused,
    Reply,
    ReplyMeta,
    public_message,
    status_for,
)

# Binders import these names from `chassis_contracts.inbound`; `__all__` re-exports them.
from chassis_contracts.inbound_cases import (
    AGENT,
    AGENT_VERSION,
    ANSWER_TEXT,
    BASE,
    BASE_REQUEST,
    CREATED,
    END_OUTPUT_CASES,
    END_OUTPUT_IDS,
    ERROR_CODES,
    IDS,
    LOGICAL_CASES,
    MID_ERROR,
    MODES,
    OTHER_IDS,
    PARTIAL,
    PRE_RUN_CODES,
    SDK_INTERFACES,
    SERVED,
    TOOL_ANSWER,
    BodyCase,
    Encode,
    Logical,
    LogicalCase,
    Mode,
    Readback,
    ReadComplete,
    ReadError,
    ReadStream,
    SSEEvent,
    Usage,
    _answer_events,
    masked,
    sse,
)

__all__ = [
    "AGENT",
    "BASE",
    "BASE_REQUEST",
    "IDS",
    "LOGICAL_CASES",
    "SERVED",
    "BodyCase",
    "Encode",
    "InboundAdapterContract",
    "Logical",
    "LogicalCase",
    "ReadComplete",
    "ReadError",
    "ReadStream",
    "Readback",
    "SSEEvent",
    "Usage",
    "masked",
    "sse",
]


async def _aiter(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


def _forbidden(name: str) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"an inbound adapter is pure: it called {name}")

    return call


@contextmanager
def _no_clock_no_ids(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`uuid.uuid4`, `uuid.uuid1`, `time.time`, and `time.time_ns` raise while active.
    Best effort: a module that bound `from uuid import uuid4` at import is not caught.
    """
    with monkeypatch.context() as m:
        m.setattr(uuid, "uuid4", _forbidden("uuid.uuid4"))
        m.setattr(uuid, "uuid1", _forbidden("uuid.uuid1"))
        m.setattr(time, "time", _forbidden("time.time"))
        m.setattr(time, "time_ns", _forbidden("time.time_ns"))
        yield


@pytest.mark.contract
class InboundAdapterContract:
    """Subclass as `Test*`; provide `adapter`, `encode`, `read_complete`, `read_stream`,
    `refused_cases`, and `ignored_cases`; set the class attributes that differ from the SDK
    formats' defaults.
    """

    model_field: ClassVar[str | None] = "model"
    """The body key that must name the agent (404 otherwise). None: the format has none."""
    errors_as_http: ClassVar[bool] = True
    """The router answers a run's `error` as HTTP through `adapter.error` (OpenAI, Anthropic).
    False for native and MCP: a run error is a 200 envelope with `status: error`."""
    finish_reason: ClassVar[str | None] = None
    """The one stop reason every normal answer gives (`stop`, `end_turn`); None: no such field."""
    output_as_text: ClassVar[bool] = True
    """An `end.output` is the answer text (section 3). False when the format returns the output
    as is (the native envelope)."""
    error_code_readable: ClassVar[bool] = True
    """An HTTP error body carries the chassis code."""
    should_retry_header: ClassVar[bool] = True
    """Error replies carry `x-should-retry` (the SDK formats)."""
    error_types: ClassVar[Mapping[int, str]] = {}
    """The format's error `type` per HTTP status (section 3); empty: not checked."""

    # --- Fixtures the binder provides ---

    @pytest.fixture
    def adapter(self) -> InboundAdapter[Any]:
        raise NotImplementedError("provide an adapter fixture: the InboundAdapter under test")

    @pytest.fixture
    def encode(self) -> Encode:
        raise NotImplementedError("provide encode(logical) -> (body, headers)")

    @pytest.fixture
    def read_complete(self) -> ReadComplete:
        raise NotImplementedError("provide read_complete(status, headers, body) -> Readback")

    @pytest.fixture
    def read_stream(self) -> ReadStream:
        raise NotImplementedError("provide read_stream(status, headers, frames) -> Readback")

    @pytest.fixture
    def refused_cases(self) -> Sequence[BodyCase]:
        raise NotImplementedError("provide refused_cases: bodies built on BASE the format refuses")

    @pytest.fixture
    def ignored_cases(self) -> Sequence[BodyCase]:
        raise NotImplementedError(
            "provide ignored_cases: bodies built on BASE that must map to BASE_REQUEST"
        )

    # --- Helpers ---

    @staticmethod
    def _map(
        adapter: InboundAdapter[Any], encode: Encode, logical: Logical, ids: Ids = IDS
    ) -> Request:
        body, headers = encode(logical)
        return adapter.to_request(body, headers, ids=ids, served=SERVED)

    def _meta(self, adapter: InboundAdapter[Any], encode: Encode, *, stream: bool) -> ReplyMeta:
        request = self._map(adapter, encode, Logical(BASE.text, max_tokens=300, stream=stream))
        return ReplyMeta(IDS, SERVED, CREATED).for_request(request)

    async def _complete(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        events: Sequence[Event],
    ) -> Readback:
        meta = self._meta(adapter, encode, stream=False)
        reply = adapter.complete(await meta.collect(events), meta)
        json.dumps(reply.body)
        return read_complete(reply.status, reply.headers, reply.body)

    async def _stream(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_stream: ReadStream,
        events: Sequence[Event],
    ) -> Readback:
        meta = self._meta(adapter, encode, stream=True)
        reply = adapter.stream(_aiter(events), meta)
        frames = [frame async for frame in reply.frames]
        assert all(isinstance(f, str) for f in frames), "frames are text"
        return read_stream(200, reply.headers, frames)

    async def _read(
        self,
        mode: Mode,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
        events: Sequence[Event],
    ) -> Readback:
        """The answer to `events` as the client of `mode` reads it."""
        if mode == "complete":
            return await self._complete(adapter, encode, read_complete, events)
        return await self._stream(adapter, encode, read_stream, events)

    def _check_code(self, read: Readback, code: str, where: str) -> None:
        """The error carries `code`, where the format makes it readable."""
        if self.error_code_readable:
            assert read.error is not None, f"{where}: the client reads no error"
            assert read.error.code == code, f"{where}: code {read.error.code!r}, want {code!r}"

    def _check_error(self, read: Readback, *, status: int, code: str | None, where: str) -> None:
        assert read.status == status, f"{where}: status {read.status}, want {status}"
        assert read.error is not None, f"{where}: the client reads no error"
        assert not read.text, f"{where}: an error reply carries answer text {read.text!r}"
        assert read.tool_calls == 0, f"{where}: an error reply carries tool calls"
        if self.error_types and status in self.error_types:
            assert read.error.type == self.error_types[status], (
                f"{where}: error type {read.error.type!r}, want {self.error_types[status]!r}"
            )
        if self.error_code_readable:
            if code is not None:
                assert read.error.code == code, f"{where}: code {read.error.code!r}, want {code!r}"
            elif status == 400:
                assert read.error.code in INVALID_BODY_CODES, (
                    f"{where}: code {read.error.code!r} is not one of {sorted(INVALID_BODY_CODES)}"
                )

    def _check_should_retry(self, read: Readback, retryable: bool, where: str) -> None:
        if self.should_retry_header:
            assert read.error is not None
            assert read.error.should_retry is retryable, (
                f"{where}: x-should-retry {read.error.should_retry!r}, want {retryable}"
            )

    # --- In: the canonical request ---

    @pytest.mark.parametrize("case", LOGICAL_CASES, ids=lambda c: c.id)
    def test_same_logical_request_gives_the_same_canonical_request(
        self, adapter: InboundAdapter[Any], encode: Encode, case: LogicalCase
    ) -> None:
        """Section 2: the tuple `(text, system, history, max_tokens, stream)` gives this exact
        `Request` once the ids are masked, in every format."""
        got = masked(self._map(adapter, encode, case.logical))
        assert got.model_dump() == case.expected.model_dump()

    async def test_to_request_is_pure(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Section 1: no clock, no minted id, no state. The same body gives the same `Request`
        and the same reply; only the ids given change the ids. The back half too."""
        assert isinstance(adapter, InboundAdapter)
        body, headers = encode(BASE)
        headers_before = dict(headers)
        with _no_clock_no_ids(monkeypatch):
            first = adapter.to_request(body, headers, ids=IDS, served=SERVED)
            second = self._map(adapter, encode, BASE)
            other = self._map(adapter, encode, BASE, OTHER_IDS)
        assert dict(headers) == headers_before, "to_request changed the headers"
        assert first == second
        assert masked(other) == masked(first)
        assert Ids.of(other) == OTHER_IDS

        meta = ReplyMeta(IDS, SERVED, CREATED).for_request(first)
        response = await meta.collect(_answer_events())
        smeta = ReplyMeta(IDS, SERVED, CREATED).for_request(
            self._map(adapter, encode, Logical(BASE.text, max_tokens=300, stream=True))
        )
        with _no_clock_no_ids(monkeypatch):
            replies = [adapter.complete(response, meta) for _ in range(2)]
            errors = [adapter.error("engine_error", "boom", False, meta) for _ in range(2)]
            streams = []
            for _ in range(2):
                reply = adapter.stream(_aiter(_answer_events()), smeta)
                streams.append([frame async for frame in reply.frames])
        assert replies[0] == replies[1], "complete is not deterministic"
        assert errors[0] == errors[1], "error is not deterministic"
        assert streams[0] == streams[1], "stream is not deterministic"

    @pytest.mark.parametrize("given", [IDS, OTHER_IDS], ids=["ids", "other-ids"])
    def test_ids_are_the_ones_given(
        self, adapter: InboundAdapter[Any], encode: Encode, given: Ids
    ) -> None:
        """Section 6: the router resolves the ids; the adapter copies exactly the ones given and
        reads none from the headers (or, for native, the body). The agent is the served one."""
        body, headers = encode(BASE)
        noisy = {
            **headers,
            "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
            "idempotency-key": "from-a-header",
            "x-request-id": "from-a-header",
        }

        request = adapter.to_request(body, noisy, ids=given, served=SERVED)

        assert Ids.of(request) == given
        assert (request.agent, request.agent_version) == (AGENT, AGENT_VERSION)

    def test_model_must_name_the_agent(
        self, adapter: InboundAdapter[Any], encode: Encode, read_complete: ReadComplete
    ) -> None:
        """Section 2 and 5: a `model` that is not the agent is 404 `model_not_found`."""
        if self.model_field is None:
            pytest.skip("this format has no model field (native names the agent in `agent`)")
        body, headers = encode(BASE)
        assert isinstance(body, dict), "a body with a model field is a dict (the SDK TypedDict)"
        body = {**body, self.model_field: "gpt-4o"}
        with pytest.raises(Refused) as info:
            adapter.to_request(body, headers, ids=IDS, served=SERVED)
        refused = info.value
        json.dumps(refused.body)
        read = read_complete(refused.status, refused.headers, refused.body)
        self._check_error(read, status=404, code="model_not_found", where="model gpt-4o")
        self._check_should_retry(read, False, "model gpt-4o")

    def test_refused_bodies_get_the_formats_error_shape(
        self,
        adapter: InboundAdapter[Any],
        read_complete: ReadComplete,
        refused_cases: Sequence[BodyCase],
    ) -> None:
        """Section 2: refuse what the client would read and not find. Each refused body is
        `Refused` with the status, the format's error body, and `x-should-retry: false`."""
        assert refused_cases, "a binding declares at least one refused body"
        for case in refused_cases:
            where = f"refused case {case.name!r}"
            try:
                adapter.to_request(case.body, case.headers, ids=IDS, served=SERVED)
            except Refused as refused:
                json.dumps(refused.body)
                read = read_complete(refused.status, refused.headers, refused.body)
                self._check_error(read, status=case.status, code=case.code, where=where)
                self._check_should_retry(read, False, where)
            else:
                pytest.fail(f"{where}: to_request mapped a body it must refuse")

    def test_ignored_parameters_do_not_change_the_canonical_request(
        self, adapter: InboundAdapter[Any], ignored_cases: Sequence[BodyCase]
    ) -> None:
        """Section 2: what only tunes the answer is accepted and ignored. Each body is `BASE`
        plus ignored parameters or headers, or `BASE` spelled another valid way, and maps to
        `BASE_REQUEST`."""
        assert ignored_cases, "a binding declares at least one ignored parameter or header"
        for case in ignored_cases:
            try:
                got = adapter.to_request(case.body, case.headers, ids=IDS, served=SERVED)
            except Refused as refused:
                pytest.fail(f"ignored case {case.name!r} was refused: {refused}")
            assert masked(got).model_dump() == BASE_REQUEST.model_dump(), (
                f"ignored case {case.name!r} changed the canonical request"
            )
            assert Ids.of(got) == IDS, f"ignored case {case.name!r} changed the ids given"

    # --- Back: the answer ---

    async def test_complete_maps_back(
        self, adapter: InboundAdapter[Any], encode: Encode, read_complete: ReadComplete
    ) -> None:
        """Section 3: the text, the usage, the one finish reason, the run's status and request
        id, as the client reads them."""
        read = await self._complete(adapter, encode, read_complete, _answer_events())
        assert read.status == 200
        assert read.error is None
        assert read.text == ANSWER_TEXT
        assert read.usage == Usage(7, 5)
        assert read.finish == self.finish_reason
        assert read.tool_calls == 0
        assert read.run_status == "ok"
        assert read.request_id in (None, IDS.request_id)

    async def test_stream_joins_to_the_complete_answer(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
    ) -> None:
        """The streamed answer joins to what complete gives: text, usage, finish, request id."""
        complete = await self._complete(adapter, encode, read_complete, _answer_events())
        streamed = await self._stream(adapter, encode, read_stream, _answer_events())
        assert streamed.status == 200
        assert streamed.error is None
        assert streamed.text == complete.text == ANSWER_TEXT
        assert streamed.usage == complete.usage
        assert streamed.finish == complete.finish == self.finish_reason
        assert streamed.tool_calls == 0
        assert streamed.run_status in (None, "ok")
        assert streamed.request_id in (None, IDS.request_id)

    async def test_usage_is_the_sum_of_metrics(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
    ) -> None:
        """Section 3 and 4: usage is the whole run's (every model call, every attempt), the
        totals `collect` gives, in both modes."""
        events: list[Event] = [
            Start(request_id=IDS.request_id),
            Metrics(input_tokens=3, output_tokens=2, attempt=1),
            Delta(text="ok"),
            Metrics(input_tokens=5, output_tokens=4, attempt=2),
            End(status="ok"),
        ]
        complete = await self._complete(adapter, encode, read_complete, events)
        streamed = await self._stream(adapter, encode, read_stream, events)
        assert complete.usage == Usage(8, 6)
        assert streamed.usage == Usage(8, 6)

    @pytest.mark.parametrize(("mode", "events", "text"), END_OUTPUT_CASES, ids=END_OUTPUT_IDS)
    async def test_end_output_without_text_is_json_text(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
        mode: Mode,
        events: list[Event],
        text: str,
    ) -> None:
        """Section 3: `end.output` without a string `text` is sent as compact, sorted JSON
        text; with one, `output.text` is the answer. Both modes, for a run with no delta; in
        complete mode, also after deltas."""
        if not self.output_as_text:
            pytest.skip("this format returns end.output as is (the native envelope)")

        read = await self._read(mode, adapter, encode, read_complete, read_stream, events)

        assert read.text == text, f"{mode}, events {events}"
        assert read.finish == self.finish_reason

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("status", ["retry", "fallback"])
    async def test_retry_and_fallback_are_a_normal_answer(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
        status: Literal["retry", "fallback"],
        mode: Mode,
    ) -> None:
        """Section 4: status `retry` or `fallback` is a normal 200 answer with the normal finish;
        complete reports the status (`x-chassis-status` or the envelope), a stream may not."""
        events: list[Event] = [
            Start(request_id=IDS.request_id),
            Delta(text="Hello."),
            Metrics(input_tokens=1, output_tokens=1),
            End(status=status),
        ]
        reported: dict[Mode, set[str | None]] = {"complete": {status}, "stream": {None, status}}

        read = await self._read(mode, adapter, encode, read_complete, read_stream, events)

        assert read.status == 200
        assert read.error is None
        assert read.text == "Hello."
        assert read.finish == self.finish_reason
        assert read.run_status in reported[mode]

    @pytest.mark.parametrize("mode", MODES)
    async def test_tool_call_events_are_not_client_tool_calls(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
        mode: Mode,
    ) -> None:
        """Section 4: the agent's own `tool_call` events are never sent as `tool_calls` or
        `tool_use` (the client would try to run them), and never change the finish reason."""
        events: list[Event] = [
            Start(request_id=IDS.request_id),
            ToolCall(
                call_id="call_1",
                name="glossary_lookup",
                arguments={"term": "SLM"},
                result={"definition": "small language model"},
            ),
            Delta(text=TOOL_ANSWER),
            Metrics(input_tokens=10, output_tokens=5),
            End(status="ok"),
        ]

        read = await self._read(mode, adapter, encode, read_complete, read_stream, events)

        assert read.tool_calls == 0, f"{mode}: a tool_call event reached the client"
        assert read.text == TOOL_ANSWER
        assert read.finish == self.finish_reason
        assert read.error is None

    # --- Errors ---

    @pytest.mark.parametrize("stream", [True, False], ids=["stream", "complete"])
    @pytest.mark.parametrize(
        ("code", "retryable"), [("model.overloaded", True), ("workload.bad_event", False)]
    )
    def test_error_before_the_first_delta_is_an_http_error(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        code: str,
        retryable: bool,
        stream: bool,
    ) -> None:
        """Section 3, the hold rule: the router hands an `error` before the first `delta` (or any
        `error` in complete mode) to `adapter.error`; the answer is an HTTP error with the
        status table's status, the format's body, the code, and `x-should-retry`."""
        if not self.errors_as_http:
            pytest.skip("this format answers a run error as a 200 envelope with status: error")
        meta = self._meta(adapter, encode, stream=stream)
        where = f"{code} (stream={stream})"

        reply = adapter.error(code, "the run failed", retryable, meta)

        json.dumps(reply.body)
        read = read_complete(reply.status, reply.headers, reply.body)
        self._check_error(read, status=status_for(code, retryable), code=code, where=where)
        assert read.error is not None
        assert "the run failed" in read.error.message, where
        assert read.finish is None, where
        self._check_should_retry(read, retryable, where)

    async def test_error_mid_stream_is_the_formats_error_frame(
        self, adapter: InboundAdapter[Any], encode: Encode, read_stream: ReadStream
    ) -> None:
        """Section 3: an `error` after text is the format's error frame, after the text that
        streamed, with no finish after it."""
        events: list[Event] = [Start(request_id=IDS.request_id), Delta(text=PARTIAL), MID_ERROR]
        read = await self._stream(adapter, encode, read_stream, events)
        assert read.status == 200
        assert read.text == PARTIAL
        assert read.error is not None, "the client reads no error"
        assert read.error.code == MID_ERROR.code
        # The SDK formats send a fixed text per code; the run's own message stays in the log and
        # the span. Native and MCP keep contract v1's message (debt: PoC-8).
        sends_public_text = adapter.interface in SDK_INTERFACES
        sent = {True: public_message(MID_ERROR.code), False: MID_ERROR.message}
        assert sent[sends_public_text] in read.error.message
        assert (MID_ERROR.message in read.error.message) is not sends_public_text, (
            f"the run's own text in {read.error.message!r}: want {not sends_public_text}"
        )
        assert read.finish is None, f"a finish ({read.finish!r}) was sent after the error"
        assert read.tool_calls == 0
        assert read.run_status in (None, "error")

    async def test_a_run_with_no_end_and_no_error_is_an_error(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        read_stream: ReadStream,
    ) -> None:
        """A run whose events stop with neither `end` nor `error` did not finish: complete mode
        is 500 `engine_error` (no answer text), stream mode ends with the format's error frame
        and no finish. Native and MCP answer it as a 200 envelope with `status: error`."""
        events: list[Event] = [Start(request_id=IDS.request_id), Delta(text=PARTIAL)]
        if not self.errors_as_http:
            meta = self._meta(adapter, encode, stream=False)
            reply = adapter.complete(await meta.collect(events), meta)
            assert reply.status == 200
            assert reply.body.get("status") == "error"
            return
        read = await self._complete(adapter, encode, read_complete, events)
        self._check_error(read, status=500, code="engine_error", where="complete, no end")
        self._check_should_retry(read, False, "complete, no end")
        streamed = await self._stream(adapter, encode, read_stream, events)
        assert streamed.status == 200
        assert streamed.error is not None, "stream, no end: the client reads no error"
        self._check_code(streamed, "engine_error", "stream, no end")
        assert streamed.finish is None, f"a finish ({streamed.finish!r}) was sent with no end"
        assert streamed.run_status in (None, "error")

    @pytest.mark.parametrize(("code", "retryable"), ERROR_CODES)
    def test_status_and_should_retry_per_error_code(
        self,
        adapter: InboundAdapter[Any],
        encode: Encode,
        read_complete: ReadComplete,
        code: str,
        retryable: bool,
    ) -> None:
        """Section 3, the status table: every `adapter.error` gets `status_for(code, retryable)`,
        the format's error type for that status, its code, and `x-should-retry`. `not_ready` and
        `invalid_body` come before `to_request`, with no request in the meta."""
        if code in PRE_RUN_CODES:
            meta = ReplyMeta(IDS, SERVED, CREATED)
        else:
            meta = self._meta(adapter, encode, stream=False)
        reply: Reply = adapter.error(code, "the message", retryable, meta)
        json.dumps(reply.body)
        read = read_complete(reply.status, reply.headers, reply.body)
        self._check_error(read, status=status_for(code, retryable), code=code, where=code)
        assert read.error is not None
        assert "the message" in read.error.message
        self._check_should_retry(read, retryable, code)
