"""`WorkloadContract`: the lane contract over A2A, for one agent, with no chassis in its env.

Subclass it as `Test*` in the agent's tests, with `agent_testkit.plugin` loaded:

```python
from agent_testkit.contract import WorkloadContract

class TestMyAgent(WorkloadContract):
    pass  # the agent comes from [tool.agent-testkit] in pyproject.toml
```

To point it at another handle, override `agent_handle_ref` in the class with
`@pytest.fixture(scope="class")`: the contract's `workload` and `chassis` fixtures are class-scoped,
so each binding gets its own pair of processes. Set `contract_input`, `terminal_types`, or
`run_statuses` if the agent's defaults differ.

Four groups of cases:

1. The agent's workload over A2A: the agent card, and one run of `contract_input` (start first
   with the request id, one terminal event last, every event valid against the vendored event
   schema), a schema version the server does not speak, and an input that is not a JSON object.
2. The agent through the chassis: `/ready`, and `/v1/run` gives an envelope with the caller's ids.
3. The lane cases over A2A (`agent_testkit.lane_cases`, vendored from `chassis_contracts.lane`),
   served by the same `workload-a2a` the agent ships with: the event types per case, and the
   stream equals the handle called directly.
4. The lane cases through the chassis: the envelope per case, JSON values and the run's
   `traceparent` reach the handle intact, and the run deadline ends a stalled run.

Every test is sync; the A2A calls run in their own event loop. No pytest-asyncio needed.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator
from typing import Any, ClassVar
from urllib.parse import urlsplit

import pytest
from workload_a2a.server import validate_event

from agent_testkit import lane_cases, wire
from agent_testkit.processes import Chassis, Workload, start_chassis, start_workload
from agent_testkit.settings import AgentRef, ChassisSource

LANE_AGENT = AgentRef(handle=lane_cases.LANE_HANDLE, name="agent-testkit-lane")
TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def canonical(value: Any) -> str:
    """JSON with sorted keys: equal only when every value and every number type is equal."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def assert_same(got: Any, want: Any, what: str) -> None:
    assert canonical(got) == canonical(want), f"{what}:\n got  {got!r}\n want {want!r}"


def _check_sequence(
    events: list[dict[str, Any]], request_id: str, terminal: tuple[str, ...]
) -> None:
    assert events, "the workload streamed no event"
    first, last = events[0], events[-1]
    assert first["type"] == "start", f"the first event is not start: {first!r}"
    assert first.get("request_id") == request_id, f"start names another request: {first!r}"
    assert last["type"] in terminal, f"the last event is not one of {terminal}: {last!r}"
    middle = [e["type"] for e in events[1:-1]]
    assert not set(middle) & {"start", "end", "error"}, f"start or a terminal mid-stream: {middle}"


class WorkloadContract:
    """The contract cases; see the module docstring."""

    contract_input: ClassVar[dict[str, Any]] = {"text": "hello big world"}
    """The input the agent's own cases send, over A2A and through the chassis."""
    terminal_types: ClassVar[tuple[str, ...]] = ("end",)
    """The last event type the agent's run of `contract_input` may end with under the fake
    model. Add `"error"` for an agent that is meant to fail on it."""
    run_statuses: ClassVar[tuple[str, ...]] = ("ok", "fallback")
    """The envelope statuses the agent's run of `contract_input` may have."""

    # --- Fixtures: one pair of processes for the agent, one for the lane cases, per class ---

    @pytest.fixture(scope="class")
    @classmethod
    def workload(cls, agent_handle_ref: AgentRef | str) -> Iterator[Workload]:
        with start_workload(agent_handle_ref) as started:
            yield started

    @pytest.fixture(scope="class")
    @classmethod
    def chassis(
        cls, workload: Workload, agent_testkit_chassis_source: ChassisSource
    ) -> Iterator[Chassis]:
        with start_chassis(workload, agent_testkit_chassis_source) as started:
            yield started

    @pytest.fixture(scope="class")
    @classmethod
    def lane_workload(cls) -> Iterator[Workload]:
        with start_workload(LANE_AGENT) as started:
            yield started

    @pytest.fixture(scope="class")
    @classmethod
    def lane_chassis(
        cls, lane_workload: Workload, agent_testkit_chassis_source: ChassisSource
    ) -> Iterator[Chassis]:
        with start_chassis(lane_workload, agent_testkit_chassis_source) as started:
            yield started

    @pytest.fixture(scope="class")
    @classmethod
    def agent_run(
        cls, workload: Workload, chassis: Chassis
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """One run of `contract_input` straight to the workload over A2A: the ctx sent and the
        events back. The chassis is up so the workload's model and tool calls reach its proxy."""
        ctx = wire.make_ctx(workload.ref.name)
        return ctx, wire.events(workload, cls.contract_input, ctx)

    # --- 1. The agent's workload over A2A ---

    def test_agent_card_names_the_handle_skill(self, workload: Workload) -> None:
        card = workload.agent_card()
        assert card["name"] == workload.ref.name
        assert [s["id"] for s in card["skills"]] == ["handle"]
        assert card["capabilities"].get("streaming") is True
        for interface in card["supportedInterfaces"]:
            host = urlsplit(interface["url"]).hostname
            assert host in LOOPBACK, f"the card sends calls off the host: {interface['url']}"

    def test_run_starts_first_and_ends_once(
        self, agent_run: tuple[dict[str, Any], list[dict[str, Any]]]
    ) -> None:
        ctx, events = agent_run
        _check_sequence(events, ctx["request_id"], self.terminal_types)

    def test_every_event_is_valid_against_the_event_schema(
        self, agent_run: tuple[dict[str, Any], list[dict[str, Any]]]
    ) -> None:
        _, events = agent_run
        for event in events:
            assert_same(validate_event(event), event, "the event as the schema fills it")

    def test_an_unsupported_schema_version_is_refused(self, workload: Workload) -> None:
        ctx = wire.make_ctx(workload.ref.name)
        events = wire.events(workload, self.contract_input, ctx, schema_version="999")
        assert events[-1]["type"] == "error", events
        assert events[-1]["code"] == "a2a.unsupported_schema_version", events

    def test_an_input_that_is_not_an_object_is_a_bad_request(self, workload: Workload) -> None:
        ctx = wire.make_ctx(workload.ref.name)
        events = wire.events(workload, self.contract_input, ctx, raw_input="[1, 2]")
        assert events[-1]["type"] == "error", events
        assert events[-1]["code"] == "a2a.bad_request", events

    # --- 2. The agent through the chassis ---

    def test_the_chassis_is_ready_in_front_of_the_workload(self, chassis: Chassis) -> None:
        with chassis.client() as http:
            assert http.get("/health").status_code == 200
            ready = http.get("/ready")
        assert ready.status_code == 200, ready.text

    def test_a_run_returns_the_envelope_with_the_callers_ids(self, chassis: Chassis) -> None:
        ids = {
            "request_id": uuid.uuid4().hex,
            "trace_id": uuid.uuid4().hex,
            "idempotency_key": uuid.uuid4().hex,
        }
        envelope = chassis.run(self.contract_input, **ids)
        for key, value in ids.items():
            assert envelope[key] == value, (key, envelope)
        assert envelope["agent"] == chassis.workload.ref.name
        assert envelope["status"] in self.run_statuses, envelope
        assert isinstance(envelope["output"], dict), envelope
        assert envelope["versions"]["chassis"], envelope

    # --- 3. The lane cases over A2A ---

    @pytest.mark.parametrize("case", list(lane_cases.TYPES))
    def test_lane_case_over_a2a(self, lane_workload: Workload, case: str) -> None:
        input = {"text": "hello big world", "data": {lane_cases.CASE_KEY: case}}
        ctx = wire.make_ctx(LANE_AGENT.name)
        events = wire.events(lane_workload, input, ctx)
        assert tuple(e["type"] for e in events) == lane_cases.TYPES[case], events
        if case in lane_cases.VERBATIM:
            direct = wire.direct(lane_cases.lane_handle, input, ctx)
            assert_same(events, [validate_event(e) for e in direct], f"{case}: A2A vs direct")

    def test_a_bad_event_is_workload_bad_event_over_a2a(self, lane_workload: Workload) -> None:
        input = {"data": {lane_cases.CASE_KEY: "bad_event"}}
        events = wire.events(lane_workload, input, wire.make_ctx(LANE_AGENT.name))
        last = events[-1]
        assert last["type"] == "error" and last["code"] == "workload.bad_event", events
        assert last["retryable"] is False, last

    # --- 4. The lane cases through the chassis ---

    @pytest.mark.parametrize("case", list(lane_cases.ENVELOPES))
    def test_lane_case_envelope_through_the_chassis(self, lane_chassis: Chassis, case: str) -> None:
        status, output = lane_cases.ENVELOPES[case]
        envelope = lane_chassis.run({"text": "hello", "data": {lane_cases.CASE_KEY: case}})
        assert envelope["status"] == status, envelope
        if output is not None:
            assert_same(envelope["output"], output, f"{case}: envelope output")
        if case == "bad_event":
            assert envelope["output"]["error"]["code"] == "workload.bad_event", envelope

    def test_json_values_and_the_traceparent_reach_the_handle(self, lane_chassis: Chassis) -> None:
        trace_id = uuid.uuid4().hex
        data = {lane_cases.CASE_KEY: "json_values", **lane_cases.JSON_VALUE}
        envelope = lane_chassis.run({"text": "hello", "data": data}, trace_id=trace_id)
        output = envelope["output"]
        assert_same(output["value"], lane_cases.JSON_VALUE, "JSON values through the chassis")
        assert_same(output["data"], data, "input.data through the chassis")
        match = TRACEPARENT.fullmatch(str(output["traceparent"]))
        assert match is not None, f"not a W3C traceparent: {output['traceparent']!r}"
        assert match.group(1) == trace_id, output["traceparent"]
        assert match.group(2) != "0" * 16, output["traceparent"]

    def test_the_run_deadline_ends_a_stalled_run(self, lane_chassis: Chassis) -> None:
        budget = {"max_tokens": 100, "timeout_ms": lane_cases.STALL_BUDGET_MS}
        began = time.monotonic()
        envelope = lane_chassis.run({"data": {lane_cases.CASE_KEY: "stalled"}}, budget=budget)
        took = time.monotonic() - began
        assert envelope["status"] == "error", envelope
        assert envelope["output"]["error"]["code"] == "a2a.timeout", envelope
        assert took < lane_cases.STALL_BOUND_S, f"the run took {took:.2f} s to end"
