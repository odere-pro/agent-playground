"""Self-test of `InboundAdapterContract`: it passes a small, correct reference format (`Toy`), and
each deliberately wrong adapter fails the test that names its mistake.

`Toy` is not a real format. It exists so the suite is checked on its own, on a format that has
everything the suite can check (a `model` field, HTTP errors, a finish reason, client tool calls,
error types), with no dependency on the chassis's own adapters. The chassis's adapters bind the
suite in `packages/chassis/tests/test_inbound_contract.py`.
"""

from __future__ import annotations

import inspect
import itertools
import json
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, ClassVar

import pytest
from chassis.core.envelope import Budget, Request, Response, TaskInput
from chassis.core.events import Delta, Error, Event, ToolCall
from chassis.core.inbound import (
    Ids,
    Interface,
    Refused,
    Reply,
    ReplyMeta,
    Served,
    StreamReply,
    status_for,
)
from chassis_contracts.inbound import (
    BASE,
    BodyCase,
    Encode,
    InboundAdapterContract,
    Logical,
    Readback,
    ReadComplete,
    ReadError,
    ReadStream,
    Usage,
    sse,
)

# --- Toy: a minimal format with everything the suite checks ---


def _error_type(status: int) -> str:
    return "client" if status < 500 else "server"


def _toy_error(code: str, message: str, retryable: bool, status: int | None = None) -> Reply:
    status = status_for(code, retryable) if status is None else status
    return Reply(
        status,
        {"x-should-retry": "true" if retryable else "false"},
        {"error": {"code": code, "message": message, "type": _error_type(status)}},
    )


def _refuse(code: str, message: str, status: int = 400) -> Refused:
    reply = _toy_error(code, message, False, status)
    return Refused(reply.status, reply.headers, reply.body)


def _answer_text(response: Response) -> str:
    text = response.output.get("text")
    if isinstance(text, str):
        return text
    return json.dumps(response.output, separators=(",", ":"), sort_keys=True)


def _usage(response: Response) -> dict[str, int]:
    return {"in": response.metrics["input_tokens"], "out": response.metrics["output_tokens"]}


def _data(payload: Any) -> str:
    return f"data: {json.dumps(payload)}\n\n"


NO_END = "the run ended without an end event"


class ToyInbound:
    """Body: `{model, system?, messages: [{role, text}], max_tokens, stream, temperature?,
    tools?}`. Complete: `{id, text, finish: "done", usage: {in, out}, tool_calls: []}`. Stream:
    `data:` frames `{delta}`, then `{finish, usage}` or `{error}`, then `data: [END]`.
    """

    interface: Interface = "native"

    def to_request(
        self, body: dict[str, Any], headers: Mapping[str, str], *, ids: Ids, served: Served
    ) -> Request:
        if body.get("model") != served.agent:
            raise _refuse("model_not_found", f"model {body.get('model')!r}", 404)
        if body.get("tools"):
            raise _refuse("unsupported_parameter", "tools")
        messages = list(body["messages"])
        if not messages or messages[-1]["role"] != "user":
            raise _refuse("unsupported_message", "the last message must be from the user")
        data: dict[str, Any] = {}
        if body.get("system"):
            data["system"] = body["system"]
        history = [{"role": m["role"], "text": m["text"]} for m in messages[:-1]]
        if history:
            data["history"] = history
        return self._request(body, ids, served, TaskInput(text=messages[-1]["text"], data=data))

    def _request(self, body: dict[str, Any], ids: Ids, served: Served, input: TaskInput) -> Request:
        return Request(
            request_id=ids.request_id,
            trace_id=ids.trace_id,
            idempotency_key=ids.idempotency_key,
            agent=served.agent,
            agent_version=served.agent_version,
            input=input,
            stream=bool(body.get("stream")),
            budget=Budget(max_tokens=body["max_tokens"]),
        )

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        if response.status == "error":  # no `end` and no `error`: the run did not finish
            return _toy_error("engine_error", NO_END, False)
        return self._answer(response)

    def _answer(self, response: Response) -> Reply:
        return Reply(
            200,
            {"x-chassis-status": response.status},
            {
                "id": response.request_id,
                "text": _answer_text(response),
                "finish": "done",
                "usage": _usage(response),
                "tool_calls": [],
            },
        )

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        async def frames() -> AsyncIterator[str]:
            seen: list[Event] = []
            sent = False
            async for event in events:
                seen.append(event)
                for frame in self._frames(event):
                    sent = True
                    yield frame
                if isinstance(event, Error):
                    yield _data({"error": {"code": event.code, "message": event.message}})
                    yield "data: [END]\n\n"
                    return
            response = await meta.collect(seen)
            if response.status == "error":
                yield _data({"error": {"code": "engine_error", "message": NO_END}})
                yield "data: [END]\n\n"
                return
            if not sent:
                yield _data({"delta": _answer_text(response)})
            yield _data({"finish": "done", "usage": _usage(response)})
            yield "data: [END]\n\n"

        return StreamReply(frames())

    def _frames(self, event: Event) -> list[str]:
        if isinstance(event, Delta):
            return [_data({"delta": event.text})]
        return []

    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        return _toy_error(code, message, retryable)


def toy_encode(logical: Logical) -> tuple[dict[str, Any], Mapping[str, str]]:
    body: dict[str, Any] = {
        "model": "echo",
        "messages": [
            *({"role": r, "text": t} for r, t in logical.history),
            {"role": "user", "text": logical.text},
        ],
        "max_tokens": logical.max_tokens,
        "stream": logical.stream,
    }
    if logical.system is not None:
        body["system"] = logical.system
    return body, {}


def toy_read_complete(status: int, headers: Mapping[str, str], body: dict[str, Any]) -> Readback:
    if status != 200:
        e = body["error"]
        retry = headers.get("x-should-retry")
        error = ReadError(
            e["message"], e["code"], e["type"], None if retry is None else retry == "true"
        )
        return Readback(None, None, None, status, error)
    return Readback(
        text=body["text"],
        usage=Usage(body["usage"]["in"], body["usage"]["out"]),
        finish=body["finish"],
        status=status,
        error=None,
        run_status=headers.get("x-chassis-status"),
        request_id=body["id"],
        tool_calls=len(body["tool_calls"]),
    )


def toy_read_stream(status: int, headers: Mapping[str, str], frames: Sequence[str]) -> Readback:
    data = [e.data for e in sse(frames)]
    assert data and data[-1] == "[END]", "the stream ends with [END]"
    text: list[str] = []
    finish = None
    usage = None
    error = None
    tool_calls = 0
    for raw in data[:-1]:
        payload = json.loads(raw)
        if "delta" in payload:
            text.append(payload["delta"])
        elif "tool_call" in payload:
            tool_calls += 1
        elif "error" in payload:
            error = ReadError(payload["error"]["message"], payload["error"]["code"])
        else:
            finish = payload["finish"]
            usage = Usage(payload["usage"]["in"], payload["usage"]["out"])
    return Readback("".join(text), usage, finish, status, error, tool_calls=tool_calls)


def _toy_with(**params: Any) -> dict[str, Any]:
    body, _ = toy_encode(BASE)
    return {**body, **params}


def toy_refused() -> list[BodyCase]:
    base = toy_encode(BASE)[0]["messages"]
    return [
        BodyCase("tools", _toy_with(tools=[{"name": "f"}]), code="unsupported_parameter"),
        BodyCase("prefill", _toy_with(messages=[*base, {"role": "assistant", "text": "x"}])),
    ]


def toy_ignored() -> list[BodyCase]:
    return [
        BodyCase("temperature", _toy_with(temperature=0.2)),
        BodyCase("auth header", _toy_with(), {"authorization": "Bearer not-a-key"}),
    ]


FIXTURES: dict[str, Any] = {
    "encode": toy_encode,
    "read_complete": toy_read_complete,
    "read_stream": toy_read_stream,
}


class TestToyInbound(InboundAdapterContract):
    """The reference: a correct adapter passes every test, none skips."""

    finish_reason: ClassVar[str | None] = "done"
    error_types: ClassVar[Mapping[int, str]] = {
        s: _error_type(s) for s in (400, 404, 500, 502, 503, 504)
    }

    @pytest.fixture
    def adapter(self) -> ToyInbound:
        return ToyInbound()

    @pytest.fixture
    def encode(self) -> Encode:
        return toy_encode

    @pytest.fixture
    def read_complete(self) -> ReadComplete:
        return toy_read_complete

    @pytest.fixture
    def read_stream(self) -> ReadStream:
        return toy_read_stream

    @pytest.fixture
    def refused_cases(self) -> Sequence[BodyCase]:
        return toy_refused()

    @pytest.fixture
    def ignored_cases(self) -> Sequence[BodyCase]:
        return toy_ignored()


# --- Deliberately wrong adapters ---


class DropsSystem(ToyInbound):
    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        request = super().to_request(body, headers, **kw)
        data = {k: v for k, v in request.input.data.items() if k != "system"}
        return request.model_copy(update={"input": TaskInput(text=request.input.text, data=data)})


class MaxTokensIgnored(ToyInbound):
    """Takes the `Budget` default instead of the body's `max_tokens`."""

    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        return super().to_request(body, headers, **kw).model_copy(update={"budget": Budget()})


class HistoryAsText(ToyInbound):
    """Folds the history into the text instead of `data.history`."""

    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        request = super().to_request(body, headers, **kw)
        turns = request.input.data.get("history", [])
        text = "\n".join([*(t["text"] for t in turns), request.input.text or ""])
        data = {k: v for k, v in request.input.data.items() if k != "history"}
        return request.model_copy(update={"input": TaskInput(text=text, data=data)})


class IdsFromHeaders(ToyInbound):
    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        request = super().to_request(body, headers, **kw)
        key = headers.get("idempotency-key", request.idempotency_key)
        return request.model_copy(update={"idempotency_key": key})


class MintsIds(ToyInbound):
    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        request = super().to_request(body, headers, **kw)
        return request.model_copy(update={"request_id": uuid.uuid4().hex})


class AnyModel(ToyInbound):
    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        return super().to_request({**body, "model": "echo"}, headers, **kw)


class AcceptsTools(ToyInbound):
    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        return super().to_request({**body, "tools": []}, headers, **kw)


class CarriesTemperature(ToyInbound):
    def to_request(self, body: dict[str, Any], headers: Mapping[str, str], **kw: Any) -> Request:
        request = super().to_request(body, headers, **kw)
        if "temperature" not in body:
            return request
        data = {**request.input.data, "temperature": body["temperature"]}
        return request.model_copy(update={"input": TaskInput(text=request.input.text, data=data)})


class LeaksToolCalls(ToyInbound):
    """Sends the agent's `tool_call` events as client tool calls, in both modes."""

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        reply = super().complete(response, meta)
        body = {**reply.body, "tool_calls": response.output.get("tool_calls", [])}
        return Reply(reply.status, reply.headers, body)

    def _frames(self, event: Event) -> list[str]:
        if isinstance(event, ToolCall):
            return [_data({"tool_call": event.name})]
        return super()._frames(event)


class LastCallUsage(ToyInbound):
    """Reports the last model call's usage, not the run's."""

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        reply = super().complete(response, meta)
        return Reply(reply.status, reply.headers, {**reply.body, "usage": {"in": 5, "out": 4}})


class EmptyForStructuredOutput(ToyInbound):
    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        reply = super().complete(response, meta)
        text = response.output.get("text")
        return Reply(
            reply.status,
            reply.headers,
            {**reply.body, "text": text if isinstance(text, str) else ""},
        )


class RetryIsAnError(ToyInbound):
    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        if response.status in ("retry", "fallback"):
            return _toy_error("engine_error", "not ok", True)
        return super().complete(response, meta)


class FinishAfterError(ToyInbound):
    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        async def frames() -> AsyncIterator[str]:
            async for event in events:
                if isinstance(event, Delta):
                    yield _data({"delta": event.text})
                elif isinstance(event, Error):
                    yield _data({"error": {"code": event.code, "message": event.message}})
            yield _data({"finish": "done", "usage": {"in": 0, "out": 0}})
            yield "data: [END]\n\n"

        return StreamReply(frames())


class EveryErrorIs500(ToyInbound):
    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        return _toy_error(code, message, retryable, 500)


class AlwaysRetry(ToyInbound):
    def error(self, code: str, message: str, retryable: bool, meta: ReplyMeta) -> Reply:
        return _toy_error(code, message, True)


class FinishLength(ToyInbound):
    """Says the answer was cut by length: a stop reason the chassis never sends."""

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        reply = super().complete(response, meta)
        return Reply(reply.status, reply.headers, {**reply.body, "finish": "length"})


class StreamDropsText(ToyInbound):
    """Loses the first delta in stream mode only."""

    def stream(self, events: AsyncIterator[Event], meta: ReplyMeta) -> StreamReply:
        async def skip_first() -> AsyncIterator[Event]:
            first = True
            async for event in events:
                if isinstance(event, Delta) and first:
                    first = False
                    continue
                yield event

        return super().stream(skip_first(), meta)


class NoEndIsAnAnswer(ToyInbound):
    """Answers a run with no `end` and no `error` as a normal 200 (the old Anthropic gap)."""

    def complete(self, response: Response, meta: ReplyMeta) -> Reply:
        return self._answer(response)


MUTANTS: list[tuple[type[ToyInbound], str]] = [
    (NoEndIsAnAnswer, "test_a_run_with_no_end_and_no_error_is_an_error"),
    (DropsSystem, "test_same_logical_request_gives_the_same_canonical_request"),
    (MaxTokensIgnored, "test_same_logical_request_gives_the_same_canonical_request"),
    (HistoryAsText, "test_same_logical_request_gives_the_same_canonical_request"),
    (IdsFromHeaders, "test_ids_are_the_ones_given"),
    (MintsIds, "test_to_request_is_pure"),
    (MintsIds, "test_ids_are_the_ones_given"),
    (AnyModel, "test_model_must_name_the_agent"),
    (AcceptsTools, "test_refused_bodies_get_the_formats_error_shape"),
    (CarriesTemperature, "test_ignored_parameters_do_not_change_the_canonical_request"),
    (LeaksToolCalls, "test_tool_call_events_are_not_client_tool_calls"),
    (LastCallUsage, "test_usage_is_the_sum_of_metrics"),
    (EmptyForStructuredOutput, "test_end_output_without_text_is_json_text"),
    (RetryIsAnError, "test_retry_and_fallback_are_a_normal_answer"),
    (FinishAfterError, "test_error_mid_stream_is_the_formats_error_frame"),
    (EveryErrorIs500, "test_status_and_should_retry_per_error_code"),
    (EveryErrorIs500, "test_error_before_the_first_delta_is_an_http_error"),
    (AlwaysRetry, "test_status_and_should_retry_per_error_code"),
    (StreamDropsText, "test_stream_joins_to_the_complete_answer"),
    (FinishLength, "test_complete_maps_back"),
    (FinishLength, "test_stream_joins_to_the_complete_answer"),
]


async def _failures(adapter: ToyInbound, name: str) -> list[str]:
    """Run one suite test, over all its parameters, on `adapter`; the parameter sets that fail."""
    suite = TestToyInbound()
    method = getattr(suite, name)
    params = list(inspect.signature(method).parameters)
    marks = [m for m in getattr(method, "pytestmark", []) if m.name == "parametrize"]
    grids: list[list[dict[str, Any]]] = []
    for mark in marks:
        argnames, values = mark.args[0], mark.args[1]
        names = [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else argnames
        grids.append([dict(zip(names, v if len(names) > 1 else (v,), strict=True)) for v in values])
    fixtures = {
        **FIXTURES,
        "adapter": adapter,
        "refused_cases": toy_refused(),
        "ignored_cases": toy_ignored(),
    }
    failed: list[str] = []
    for combo in itertools.product(*grids) if grids else [()]:
        kwargs: dict[str, Any] = {}
        for part in combo:
            kwargs.update(part)
        monkeypatch = pytest.MonkeyPatch()
        call = {**{p: fixtures[p] for p in params if p in fixtures}, **kwargs}
        if "monkeypatch" in params:
            call["monkeypatch"] = monkeypatch
        try:
            result = method(**call)
            if inspect.isawaitable(result):
                await result
        except pytest.skip.Exception as exc:
            raise AssertionError(f"{name} skipped on the toy format: {exc}") from exc
        except (Exception, pytest.fail.Exception) as exc:
            failed.append(f"{kwargs}: {type(exc).__name__}: {exc}")
        finally:
            monkeypatch.undo()
    return failed


@pytest.mark.parametrize(("mutant", "test"), MUTANTS, ids=lambda x: getattr(x, "__name__", x))
async def test_the_suite_catches_a_wrong_adapter(mutant: type[ToyInbound], test: str) -> None:
    """Each wrong adapter fails the suite test that names its mistake."""
    assert await _failures(mutant(), test), f"{mutant.__name__} passed {test}"


@pytest.mark.parametrize("test", sorted({t for _, t in MUTANTS}))
async def test_the_reference_passes_what_the_mutants_fail(test: str) -> None:
    """The same driver finds no failure on the reference, so a mutant's failure is its own."""
    assert await _failures(ToyInbound(), test) == []


def test_every_suite_test_has_a_mutant() -> None:
    """No suite test goes unchecked: each one is failed by at least one wrong adapter."""
    tests = {n for n in dir(InboundAdapterContract) if n.startswith("test_")}
    covered = {t for _, t in MUTANTS}
    assert tests - covered == set(), sorted(tests - covered)
