"""LaneContract: the same `handle` gives the same event stream and the same envelope in every lane.

A binder names its lanes in `lane_labels` and provides a `lanes` fixture: a mapping from each label
(for example `inprocess`, `sidecar`, `sidecar-workload-server`) to a factory. A factory takes a
handle path, `module:attribute`, and is an async context manager that yields an `EngineConnector`
already set up on that handle; on exit it closes the connector and stops anything it started (a
server on a socket, say). The path, not the callable, is what a lane gets, because `inprocess`
loads its handle by path; a lane with its own server loads it with
`chassis.adapters.a2a.inprocess.load_handle`.

Each case runs one handle over every lane and compares the lanes pairwise: the exact event list
(`model_dump()` of each event after `parse_event`) and the `Response` that `collect` builds with the
same `Request` and `Versions`. `Response` holds nothing per-run (no latency, no timestamps; a
`metrics.latency_ms` is the handle's own value), so the envelope is compared whole. Two values are
legitimately per-run or per-server and are masked, in the events and in the envelope alike:

- the parent id of a `traceparent` a handle echoes back in `end.output` (the connector's run span,
  new on every run); its version, trace id, and flags are still compared, and
  `test_the_run_traceparent_reaches_handle` checks the whole value per lane;
- the `message` of a `workload.bad_event` error: the chassis template server words it from
  pydantic, `workload_a2a` from jsonschema, the TypeScript server from its own validator. The
  code, the retryable flag, and the event's place in the stream are still compared.

Spec: docs/contracts/contract-v0.md, "Chassis events over A2A" and "Changes decided for v1".
"""

from __future__ import annotations

import asyncio
import importlib
import itertools
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from chassis import CHASSIS_VERSION
from chassis.core.collector import collect
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.events import End, Error, Event, parse_event
from chassis.core.trace import trace_id_hex
from chassis.ports.engine import LANES, EngineConnector

from chassis_contracts.engine import JSON_VALUE, JSON_VALUES_HANDLE, TRACEPARENT
from chassis_contracts.helpers import make_context, make_request

LaneFactory = Callable[[str], AbstractAsyncContextManager[EngineConnector]]
"""A handle path in, a set-up `EngineConnector` out; closed, with its server, on exit."""

LANE_TIMEOUT_S = 30.0
"""suggested: one run of one case in one lane; a lane that hangs fails instead."""

RUN_SPAN = "<run span>"
BAD_EVENT_MESSAGE = "<validator message>"
BAD_EVENT_CODE = "workload.bad_event"


# --- The case handles, in the wire form: plain dicts, no chassis import at run time ---


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": "0", "type": kind, **fields}


DELTAS = ("one ", "two ", "three ", "four ", "five")


async def deltas_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Five deltas, then `metrics` with every field set, then `end`."""
    yield _event("start", request_id=ctx["request_id"])
    for text in DELTAS:
        yield _event("delta", text=text)
    yield _event(
        "metrics",
        input_tokens=7,
        output_tokens=5,
        cost_usd=0.25,
        model_route=ctx.get("model_route"),
        latency_ms=12,
        attempt=2,
    )
    yield _event("end", status="ok")


TOOL_ARGUMENTS: dict[str, Any] = {"term": "SLM", "limit": 2}
TOOL_RESULT: dict[str, Any] = {"definition": "small language model", "hits": 1, "score": 0.5}


async def tool_call_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A `tool_call` with its `result`, then the answer, `metrics`, and `end`."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event(
        "tool_call",
        call_id="call_1",
        name="glossary_lookup",
        arguments=TOOL_ARGUMENTS,
        result=TOOL_RESULT,
    )
    yield _event("delta", text="SLM means small language model.")
    yield _event("metrics", input_tokens=10, output_tokens=5, attempt=1)
    yield _event("end", status="ok")


async def error_handle(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """One delta, then a retryable `error`."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event("delta", text="partial")
    yield _event("error", code="model.overloaded", message="try again later", retryable=True)


END_OUTPUT: dict[str, Any] = {"text": "set by handle", "score": 2, "tags": ["a", "b"], "p": 0.5}


async def end_output_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A delta, then `end` with `output` set and status `fallback`: `collect` keeps the output."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event("delta", text="not the output")
    yield _event("end", status="fallback", output=END_OUTPUT)


async def bad_event_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A `delta` with no `text`: every server must stop the run with `workload.bad_event`."""
    yield _event("start", request_id=ctx["request_id"])
    yield _event("delta")
    yield _event("delta", text="never sent")
    yield _event("end", status="ok")


async def no_schema_version_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Events with no `schema_version`: contract v1 fills it with `"0"` in every server."""
    yield {"type": "start", "request_id": ctx["request_id"]}
    yield {"type": "delta", "text": "no version"}
    yield {"type": "end", "status": "ok"}


STALL_S = 2.0
"""How long `stalled_handle` sleeps after `start`: well past `STALL_BUDGET_MS`."""
STALL_BUDGET_MS = 200
"""The run budget of the deadline case (suggested)."""
STALL_BOUND_S = 1.5
"""The stream of the deadline case must end within this, well before the handle wakes up."""


async def stalled_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """`start`, then a sleep past the run budget: only the connector's run deadline ends it."""
    yield _event("start", request_id=ctx["request_id"])
    await asyncio.sleep(STALL_S)
    yield _event("end", status="ok")


@dataclass(frozen=True)
class LaneCase:
    """One handle the suite runs over every lane."""

    path: str
    """`module:attribute`."""
    types: tuple[str, ...]
    """The event types every lane must give, in order."""
    verbatim: bool = True
    """Every lane must give exactly what the handle yields when called directly. False when the
    lane changes the stream on purpose (a bad event) or the handle echoes a per-run value."""


_HERE = __name__
CASES: dict[str, LaneCase] = {
    "echo": LaneCase(
        "chassis.core.handle:echo_wire",
        ("start", "delta", "delta", "delta", "metrics", "end"),
    ),
    "deltas": LaneCase(
        f"{_HERE}:deltas_handle", ("start", *["delta"] * len(DELTAS), "metrics", "end")
    ),
    "json_values": LaneCase(JSON_VALUES_HANDLE, ("start", "tool_call", "end"), verbatim=False),
    "tool_call": LaneCase(
        f"{_HERE}:tool_call_handle", ("start", "tool_call", "delta", "metrics", "end")
    ),
    "error": LaneCase(f"{_HERE}:error_handle", ("start", "delta", "error")),
    "end_output": LaneCase(f"{_HERE}:end_output_handle", ("start", "delta", "end")),
    "bad_event": LaneCase(f"{_HERE}:bad_event_handle", ("start", "error"), verbatim=False),
    "no_schema_version": LaneCase(f"{_HERE}:no_schema_version_handle", ("start", "delta", "end")),
}
"""Cases with the same stream in every lane. The run-deadline case is not here: where the
deadline fires differs per lane (`inprocess` gets no event before it), so it has its own test."""

STALLED_HANDLE = f"{_HERE}:stalled_handle"


# --- Running and comparing ---


def lane_request() -> Request:
    """The request every case sends: text for the echo, `JSON_VALUE` in `input.data`, a budget."""
    return make_request(text="hello big world").model_copy(
        update={
            "input": TaskInput(text="hello big world", data=JSON_VALUE),
            "budget": Budget(max_tokens=1234, timeout_ms=30000),
        }
    )


LANE_VERSIONS = Versions(chassis=CHASSIS_VERSION, model_route="fake-route")


@dataclass
class LaneRun:
    events: list[Event]
    envelope: dict[str, Any]

    def comparable(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """The events as `model_dump()` and the envelope, with the per-run values masked."""
        events = [_mask_event(e.model_dump()) for e in self.events]
        envelope = dict(self.envelope)
        output = dict(envelope.get("output") or {})
        if isinstance(output.get("traceparent"), str):
            output["traceparent"] = _mask_traceparent(output["traceparent"])
        error = output.get("error")
        if isinstance(error, dict) and error.get("code") == BAD_EVENT_CODE:
            output["error"] = {**error, "message": BAD_EVENT_MESSAGE}
        envelope["output"] = output
        return events, envelope


def _mask_traceparent(value: str) -> str:
    match = TRACEPARENT.fullmatch(value)
    if match is None:
        return value
    return f"00-{match.group(1)}-{RUN_SPAN}-{match.group(3)}"


def _mask_event(dump: dict[str, Any]) -> dict[str, Any]:
    if dump["type"] == "end" and isinstance(dump.get("output"), dict):
        output = dict(dump["output"])
        if isinstance(output.get("traceparent"), str):
            output["traceparent"] = _mask_traceparent(output["traceparent"])
        return {**dump, "output": output}
    if dump["type"] == "error" and dump["code"] == BAD_EVENT_CODE:
        return {**dump, "message": BAD_EVENT_MESSAGE}
    return dump


async def _replay(events: Sequence[Event]) -> AsyncIterator[Event]:
    for event in events:
        yield event


async def run_over(
    factory: LaneFactory, path: str, request: Request, ctx: Context, versions: Versions
) -> LaneRun:
    """One run of `path` in one lane: every event re-parsed from its wire form, and the envelope."""
    async with asyncio.timeout(LANE_TIMEOUT_S), factory(path) as engine:
        events = [parse_event(e.model_dump(mode="json")) async for e in engine.run(request, ctx)]
    envelope = await collect(_replay(events), request, versions)
    return LaneRun(events, envelope.model_dump())


async def run_direct(path: str, request: Request, ctx: Context) -> list[Event]:
    """The handle called with no lane at all: what it yields, parsed."""
    module, _, attr = path.partition(":")
    handle = getattr(importlib.import_module(module), attr)
    out: list[Event] = []
    async for raw in handle(request.input.model_dump(mode="json"), ctx.model_dump(mode="json")):
        dump = getattr(raw, "model_dump", None)
        out.append(parse_event(dump(mode="json") if callable(dump) else dict(raw)))
    return out


def _canonical(value: Any) -> str:
    """JSON with sorted keys: equal only when every value and every number type is equal. Plain
    `==` would pass a lane that turns `2` into `2.0`."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _dumps(events: Sequence[Event]) -> list[dict[str, Any]]:
    return [e.model_dump() for e in events]


def assert_same(a: Any, b: Any, what: str) -> None:
    """`a` and `b` are the same JSON value, number types included; the message names the first
    event (or field) that differs."""
    if _canonical(a) == _canonical(b):
        return
    if isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(itertools.zip_longest(a, b)):
            if _canonical(x) != _canonical(y):
                raise AssertionError(f"{what}: first difference at event {i}: {x!r} != {y!r}")
    raise AssertionError(f"{what}: {a!r} != {b!r}")


@pytest.mark.contract
class LaneContract:
    """Subclass as `Test*`: set `lane_labels` and provide `lanes` (see the module docstring).
    Override `run_request` or `versions` if the defaults do not suit the lanes.
    """

    lane_labels: ClassVar[tuple[str, ...]] = ()
    """The keys of `lanes`, known at collection time so the per-lane cases carry the label in
    their id (for example `test_the_run_traceparent_reaches_handle[sidecar]`)."""

    def pytest_generate_tests(self, metafunc: pytest.Metafunc) -> None:
        if "case" in metafunc.fixturenames:
            metafunc.parametrize("case", list(CASES), ids=list(CASES))
        if "lane" in metafunc.fixturenames:
            labels = type(self).lane_labels
            if len(labels) < 2:
                raise TypeError(
                    f"{type(self).__name__}: set lane_labels to two or more lane names; "
                    "the suite compares lanes with each other"
                )
            metafunc.parametrize("lane", list(labels), ids=list(labels))

    @pytest.fixture
    def lanes(self) -> Mapping[str, LaneFactory]:
        raise NotImplementedError("provide a lanes fixture: {label: factory(handle_path)}")

    @pytest.fixture
    def run_request(self) -> Request:
        return lane_request()

    @pytest.fixture
    def context(self, run_request: Request) -> Context:
        return make_context(run_request)

    @pytest.fixture
    def versions(self) -> Versions:
        return LANE_VERSIONS

    def test_lanes_match_their_labels(self, lanes: Mapping[str, LaneFactory]) -> None:
        assert sorted(lanes) == sorted(self.lane_labels), "lanes and lane_labels disagree"

    async def test_each_lane_is_the_connector_it_names(
        self, lanes: Mapping[str, LaneFactory], lane: str
    ) -> None:
        """The label starts with the connector's `kind`, and the connector streams."""
        async with asyncio.timeout(LANE_TIMEOUT_S), lanes[lane](CASES["echo"].path) as engine:
            assert engine.kind in LANES
            assert lane.startswith(engine.kind), f"{lane!r} is a {engine.kind!r} connector"
            assert "streaming" in engine.capabilities

    async def test_same_events_and_envelope_over_every_lane(
        self,
        lanes: Mapping[str, LaneFactory],
        case: str,
        run_request: Request,
        context: Context,
        versions: Versions,
    ) -> None:
        """Every pair of lanes gives the same events, in order, and the same envelope."""
        spec = CASES[case]
        runs = {
            label: await run_over(factory, spec.path, run_request, context, versions)
            for label, factory in lanes.items()
        }
        for label, run in runs.items():
            assert tuple(e.type for e in run.events) == spec.types, (label, run.events)
        for (a, run_a), (b, run_b) in itertools.combinations(runs.items(), 2):
            events_a, envelope_a = run_a.comparable()
            events_b, envelope_b = run_b.comparable()
            assert_same(events_a, events_b, f"events, {a} vs {b}")
            assert_same(envelope_a, envelope_b, f"envelope, {a} vs {b}")
        if spec.verbatim:
            direct = await run_direct(spec.path, run_request, context)
            for label, run in runs.items():
                assert_same(_dumps(run.events), _dumps(direct), f"{label} vs the handle itself")

    async def test_a_bad_event_is_workload_bad_event_in_every_lane(
        self,
        lanes: Mapping[str, LaneFactory],
        run_request: Request,
        context: Context,
        versions: Versions,
    ) -> None:
        """The server stops the run at the bad event; nothing the handle yields after it crosses."""
        for label, factory in lanes.items():
            run = await run_over(factory, CASES["bad_event"].path, run_request, context, versions)
            last = run.events[-1]
            assert isinstance(last, Error), (label, run.events)
            assert last.code == BAD_EVENT_CODE and last.retryable is False, (label, last)
            assert run.envelope["status"] == "error", (label, run.envelope)

    async def test_error_and_end_output_cross_intact(
        self,
        lanes: Mapping[str, LaneFactory],
        run_request: Request,
        context: Context,
        versions: Versions,
    ) -> None:
        """A retryable `error` stays retryable; an `end.output` is the envelope's output."""
        for label, factory in lanes.items():
            failed = await run_over(factory, CASES["error"].path, run_request, context, versions)
            error = failed.events[-1]
            assert isinstance(error, Error) and error.retryable is True, (label, error)
            assert failed.envelope["output"] == {
                "text": "partial",
                "error": {"code": "model.overloaded", "message": "try again later"},
            }, label
            done = await run_over(factory, CASES["end_output"].path, run_request, context, versions)
            end = done.events[-1]
            assert isinstance(end, End) and end.status == "fallback", (label, end)
            assert done.envelope["output"] == END_OUTPUT, label
            assert done.envelope["status"] == "fallback", label

    async def test_the_run_deadline_ends_a_stalled_run_in_every_lane(
        self, lanes: Mapping[str, LaneFactory], lane: str, context: Context
    ) -> None:
        """`budget.timeout_ms` is a deadline for the whole run, in every lane: a handle that
        yields `start` and then stalls past it ends in one retryable `a2a.timeout`, and the
        stream ends well before the handle would wake up. `inprocess` may get no event before
        the deadline (its events arrive in one batch), so only the last event is compared.
        """
        request = lane_request().model_copy(
            update={"budget": Budget(max_tokens=1234, timeout_ms=STALL_BUDGET_MS)}
        )
        ctx = context.model_copy(update={"budget": request.budget})
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(LANE_TIMEOUT_S), lanes[lane](STALLED_HANDLE) as engine:
            began = loop.time()
            events = [e async for e in engine.run(request, ctx)]
            took = loop.time() - began
        assert events, f"{lane}: no events"
        last = events[-1]
        assert isinstance(last, Error) and last.code == "a2a.timeout", (lane, events)
        assert last.retryable is True, (lane, last)
        assert not any(isinstance(e, End) for e in events), (lane, events)
        assert took < STALL_BOUND_S, f"{lane}: the stream took {took:.2f} s to end"

    async def test_the_run_traceparent_reaches_handle(
        self,
        lanes: Mapping[str, LaneFactory],
        lane: str,
        run_request: Request,
        context: Context,
        versions: Versions,
    ) -> None:
        """`ctx["traceparent"]` as `handle` sees it is W3C version `00` with the run's trace id,
        `trace_id_hex(ctx.trace_id)`, and a parent id that is not all zero.
        """
        run = await run_over(lanes[lane], JSON_VALUES_HANDLE, run_request, context, versions)
        end = run.events[-1]
        assert isinstance(end, End) and end.output is not None, run.events
        value = end.output["traceparent"]
        assert isinstance(value, str), f"{lane}: ctx.traceparent did not reach handle: {value!r}"
        match = TRACEPARENT.fullmatch(value)
        assert match is not None, f"{lane}: not a W3C traceparent: {value!r}"
        assert match.group(1) == trace_id_hex(context.trace_id), value
        assert match.group(2) != "0" * 16, value
