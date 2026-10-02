"""Self-test of the lane suite: it passes over two in-memory lanes (no socket), its case handles
yield what they declare, and its comparison catches the changes a lane must not make.
The socket lanes bind it in `packages/chassis/tests/test_lane_contract.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis_contracts import CASES, LaneContract, LaneFactory
from chassis_contracts.helpers import make_context
from chassis_contracts.lane import (
    BAD_EVENT_MESSAGE,
    RUN_SPAN,
    LaneRun,
    assert_same,
    lane_request,
    run_direct,
)


@asynccontextmanager
async def _inprocess(path: str) -> AsyncIterator[EngineConnector]:
    connector = InProcessConnector()
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    await connector.setup({"connector": "inprocess", "handle": path}, bundle)
    try:
        yield connector
    finally:
        await connector.close()


class TestTwoInProcessLanes(LaneContract):
    """Two separate in-memory servers: the suite itself holds with no socket at all."""

    lane_labels: ClassVar[tuple[str, ...]] = ("inprocess", "inprocess-second")

    @pytest.fixture
    def lanes(self) -> Mapping[str, LaneFactory]:
        return {"inprocess": _inprocess, "inprocess-second": _inprocess}


@pytest.mark.parametrize("name", [n for n, c in CASES.items() if n != "bad_event"])
async def test_each_case_handle_yields_its_declared_types(name: str) -> None:
    """Called directly, with no lane, a handle yields the types its case declares; the bad-event
    handle is the one the servers cut short, so it is left out."""
    request = lane_request()
    events = await run_direct(CASES[name].path, request, make_context(request))
    assert tuple(e.type for e in events) == CASES[name].types


def test_assert_same_tells_an_int_from_a_float() -> None:
    assert_same([{"n": 2}], [{"n": 2}], "same")
    with pytest.raises(AssertionError, match="first difference at event 0"):
        assert_same([{"n": 2}], [{"n": 2.0}], "int vs float")
    with pytest.raises(AssertionError):
        assert_same({"n": True}, {"n": 1}, "bool vs int")


def test_comparable_masks_only_the_per_run_values() -> None:
    """The run span in an echoed `traceparent` and a bad event's message are masked; the trace
    id, the flags, and every other field stay."""
    from chassis.core.events import End, Error, Start

    traceparent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    run = LaneRun(
        events=[
            Start(request_id="r"),
            End(output={"traceparent": traceparent, "n": 1}),
            Error(code="workload.bad_event", message="pydantic says no"),
            Error(code="other", message="kept"),
        ],
        envelope={"output": {"traceparent": traceparent, "error": {"code": "workload.bad_event"}}},
    )
    events, envelope = run.comparable()
    masked = f"00-{'a' * 32}-{RUN_SPAN}-01"
    assert events[1]["output"] == {"traceparent": masked, "n": 1}
    assert events[2]["message"] == BAD_EVENT_MESSAGE
    assert events[3]["message"] == "kept"
    assert envelope["output"]["traceparent"] == masked
    assert envelope["output"]["error"] == {
        "code": "workload.bad_event",
        "message": BAD_EVENT_MESSAGE,
    }


def test_a_binding_with_one_lane_is_refused() -> None:
    class OneLane(LaneContract):
        lane_labels: ClassVar[tuple[str, ...]] = ("inprocess",)

    class Metafunc:
        fixturenames: ClassVar[list[str]] = ["lane"]

        def parametrize(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("must refuse before parametrizing")

    with pytest.raises(TypeError, match="two or more"):
        OneLane().pytest_generate_tests(Metafunc())  # type: ignore[arg-type]
