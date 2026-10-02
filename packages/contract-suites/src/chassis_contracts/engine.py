"""EngineConnectorContract: the stream starts with `start`, ends with `end` or `error`, and cancels
cleanly. Over a lane, JSON values keep their integers and the run's `traceparent` reaches `handle`
in `ctx` (contract-v0.md, "Changes decided for v1", items 1 and 4).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from chassis.core.envelope import Budget, Context, Request, TaskInput
from chassis.core.events import End, Error, Metrics, Start, ToolCall, parse_event
from chassis.core.trace import trace_id_hex
from chassis.ports.engine import LANES, EngineConnector

from chassis_contracts.helpers import make_context, make_request

JSON_VALUE: dict[str, Any] = {"n": 3, "big": 9007199254740991, "f": 1.5, "nested": [1, {"k": 2}]}
"""The value the "JSON values survive the lane" case sends both ways: `big` is 2^53 - 1."""

JSON_VALUES_HANDLE = "chassis_contracts.engine:json_values_handle"
"""`json_values_handle` as `module:attribute`, for a lane that loads the handle by path."""

TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")


async def json_values_handle(
    input: dict[str, Any], ctx: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """A wire-form `handle` for the JSON and trace cases: a `tool_call` whose `arguments` are
    `JSON_VALUE`, and an `end` whose `output` echoes `input.data`, `ctx.budget`, and
    `ctx.traceparent` next to `JSON_VALUE`. Plain dicts, no chassis import at run time.
    """
    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    yield {
        "schema_version": "0",
        "type": "tool_call",
        "call_id": "c1",
        "name": "json_values",
        "arguments": JSON_VALUE,
    }
    yield {
        "schema_version": "0",
        "type": "end",
        "status": "ok",
        "output": {
            "value": JSON_VALUE,
            "data": input.get("data"),
            "budget": ctx.get("budget"),
            "traceparent": ctx.get("traceparent"),
        },
    }


def assert_same_json(got: Any, want: Any, path: str = "$") -> None:
    """Equal, and every number has the same Python type: `3` is not `3.0`."""
    assert type(got) is type(want), f"{path}: {got!r} ({type(got).__name__}) is not {want!r}"
    if isinstance(want, dict):
        assert got.keys() == want.keys(), f"{path}: keys {sorted(got)} != {sorted(want)}"
        for key in want:
            assert_same_json(got[key], want[key], f"{path}.{key}")
    elif isinstance(want, list):
        assert len(got) == len(want), f"{path}: length {len(got)} != {len(want)}"
        for i, (g, w) in enumerate(zip(got, want, strict=True)):
            assert_same_json(g, w, f"{path}[{i}]")
    else:
        assert got == want, f"{path}: {got!r} != {want!r}"


@pytest.mark.contract
class EngineConnectorContract:
    """Subclass as `Test*`, provide `engine` (already set up). Override `run_request` if the echo
    text is not enough.
    """

    @pytest.fixture
    def engine(self) -> EngineConnector:
        raise NotImplementedError("provide an engine fixture")

    @pytest.fixture
    def run_request(self) -> Request:
        return make_request()

    @pytest.fixture
    def json_values_engine(self) -> EngineConnector:
        """Optional: an engine, already set up, whose workload is `json_values_handle`
        (`JSON_VALUES_HANDLE` by path). The JSON and trace cases skip without it.
        """
        pytest.skip("bind json_values_engine to run the JSON and traceparent cases")

    @pytest.fixture
    def context(self, run_request: Request) -> Context:
        return make_context(run_request)

    def test_declares_lane_and_capabilities(self, engine: EngineConnector) -> None:
        assert engine.kind in LANES
        assert "streaming" in engine.capabilities

    async def test_probe_is_healthy_when_the_workload_answers(
        self, engine: EngineConnector
    ) -> None:
        """`probe()` (contract v3) is `True` for a set-up engine whose workload answers. It
        returns a `bool` and never raises; the readiness monitor counts `False` as a failure.
        """
        assert await engine.probe() is True

    async def test_stream_starts_and_ends(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        events = [e async for e in engine.run(run_request, context)]
        assert events, "no events"
        assert isinstance(events[0], Start) and events[0].request_id == run_request.request_id
        assert isinstance(events[-1], End | Error)
        assert not any(isinstance(e, Start) for e in events[1:]), "start must come once"

    async def test_events_survive_the_wire(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        async for event in engine.run(run_request, context):
            wire = event.model_dump(mode="json")
            assert parse_event(wire) == event

    async def test_metrics_keep_their_integers(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        """A lane may carry numbers as doubles (protobuf `Struct`); token counts come back as
        `int`. Skips, rather than passing for nothing, when the binding emits no `metrics`.
        """
        seen = False
        async for event in engine.run(run_request, context):
            if isinstance(event, Metrics):
                seen = True
                assert type(event.input_tokens) is int and type(event.output_tokens) is int
                assert type(event.attempt) is int
        if not seen:
            pytest.skip("this binding emits no metrics")

    async def test_cancel_is_clean(
        self, engine: EngineConnector, run_request: Request, context: Context
    ) -> None:
        stream = engine.run(run_request, context)
        first = await anext(stream)
        assert isinstance(first, Start)
        closer = getattr(stream, "aclose", None)
        if closer is not None:
            await closer()
        await engine.close()

    async def _json_run(self, engine: EngineConnector) -> tuple[Context, ToolCall, End]:
        request = make_request().model_copy(
            update={
                "input": TaskInput(text="json", data=JSON_VALUE),
                "budget": Budget(max_tokens=1234, timeout_ms=30000),
            }
        )
        ctx = make_context(request)
        events = [e async for e in engine.run(request, ctx)]
        assert [e.type for e in events] == ["start", "tool_call", "end"], events
        tool, end = events[1], events[2]
        assert isinstance(tool, ToolCall) and isinstance(end, End)
        assert end.output is not None
        return ctx, tool, end

    async def test_json_values_survive_the_lane(self, json_values_engine: EngineConnector) -> None:
        """Ints stay `int` both ways: in `tool_call.arguments` and `end.output` coming back, and
        in `input.data` and `ctx.budget.max_tokens` going in (A2A `Struct` numbers are doubles;
        the chassis JSON crosses as a string).
        """
        ctx, tool, end = await self._json_run(json_values_engine)
        assert end.output is not None
        assert_same_json(tool.arguments, JSON_VALUE, "tool_call.arguments")
        assert_same_json(end.output["value"], JSON_VALUE, "end.output")
        assert_same_json(end.output["data"], JSON_VALUE, "input.data")
        assert_same_json(end.output["budget"], ctx.budget.model_dump(mode="json"), "ctx.budget")
        assert type(end.output["budget"]["max_tokens"]) is int

    async def test_the_run_traceparent_reaches_handle(
        self, json_values_engine: EngineConnector
    ) -> None:
        """The connector sets `ctx.traceparent` per run: W3C version `00`, the run's trace id."""
        ctx, _, end = await self._json_run(json_values_engine)
        assert end.output is not None
        value = end.output["traceparent"]
        assert isinstance(value, str), f"ctx.traceparent did not reach handle: {value!r}"
        match = TRACEPARENT.fullmatch(value)
        assert match is not None, value
        assert match.group(1) == trace_id_hex(ctx.trace_id)
        assert match.group(2) != "0" * 16
