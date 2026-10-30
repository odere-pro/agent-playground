"""The vendored lane cases still equal the originals in `chassis_contracts` (monorepo only).

The kit cannot import `chassis_contracts` (it pulls in `chassis`), so `lane_cases` is a copy. Here,
where both are installed, each copied handle must yield what its original yields, and `ENVELOPES`
must be what the chassis's `collect` builds from those events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from agent_testkit import lane_cases

lane = pytest.importorskip("chassis_contracts.lane")
engine = pytest.importorskip("chassis_contracts.engine")
collector = pytest.importorskip("chassis.core.collector")
events_module = pytest.importorskip("chassis.core.events")
helpers = pytest.importorskip("chassis_contracts.helpers")

ORIGINALS: dict[str, Any] = {
    "deltas": lane.deltas_handle,
    "tool_call": lane.tool_call_handle,
    "error": lane.error_handle,
    "end_output": lane.end_output_handle,
    "bad_event": lane.bad_event_handle,
    "no_schema_version": lane.no_schema_version_handle,
    "json_values": engine.json_values_handle,
    "stalled": lane.stalled_handle,
}


async def _all(handle: Any, input: dict[str, Any], ctx: dict[str, Any]) -> list[Any]:
    return [e async for e in handle(input, ctx)]


def test_every_case_has_an_original() -> None:
    assert sorted(ORIGINALS) == sorted(lane_cases.HANDLES)


@pytest.mark.parametrize("case", sorted(set(ORIGINALS) - {"stalled"}))
async def test_the_copy_yields_what_the_original_yields(case: str) -> None:
    request = lane.lane_request()
    input = request.input.model_dump(mode="json")
    ctx = helpers.make_context(request).model_dump(mode="json")
    copy = await _all(lane_cases.HANDLES[case], input, ctx)
    assert copy == await _all(ORIGINALS[case], input, ctx)


def test_the_constants_match() -> None:
    assert lane_cases.JSON_VALUE == engine.JSON_VALUE
    assert (lane_cases.STALL_S, lane_cases.STALL_BUDGET_MS, lane_cases.STALL_BOUND_S) == (
        lane.STALL_S,
        lane.STALL_BUDGET_MS,
        lane.STALL_BOUND_S,
    )
    for case, types in lane_cases.TYPES.items():
        assert types == lane.CASES[case].types, case


@pytest.mark.parametrize("case", sorted(lane_cases.ENVELOPES))
async def test_the_envelope_table_is_what_collect_builds(case: str) -> None:
    request = lane.lane_request()
    ctx = helpers.make_context(request).model_dump(mode="json")
    raw = await _all(lane_cases.HANDLES[case], request.input.model_dump(mode="json"), ctx)
    parsed = []
    for event in raw:
        try:
            parsed.append(events_module.parse_event(event))
        except ValueError:  # the bad event: the server turns it into workload.bad_event
            parsed.append(events_module.Error(code="workload.bad_event", message="bad"))
            break

    async def replay() -> AsyncIterator[Any]:
        for event in parsed:
            yield event

    envelope = await collector.collect(replay(), request, lane.LANE_VERSIONS)
    status, output = lane_cases.ENVELOPES[case]
    assert envelope.status == status
    if output is not None:
        assert envelope.output == output
