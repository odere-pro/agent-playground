"""Request correlation: `traceparent` parsing and the registry of in-flight runs that the model
proxy charges by trace id (PoC-2: "two concurrent requests in one replica each get their own
budget").
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from chassis import CHASSIS_VERSION
from chassis.core import trace as core_trace
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.trace import trace_id_hex
from chassis.ports.model import Usage
from chassis.server import correlation
from chassis.server.correlation import RunRecord, RunRegistry, TraceIdInUse, parse_traceparent

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
VALID = f"00-{TRACE}-00f067aa0ba902b7-01"


def _request(request_id: str, trace_id: str, max_tokens: int = 20) -> tuple[Request, Context]:
    request = Request(
        request_id=request_id,
        trace_id=trace_id,
        idempotency_key=f"idem-{request_id}",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=max_tokens),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
    )
    return request, ctx


def test_a_valid_traceparent_gives_the_trace_id() -> None:
    assert len(VALID) == 55
    assert parse_traceparent(VALID) == TRACE
    assert parse_traceparent(f"  {VALID.upper()} ") == TRACE
    # A later version may append fields; the first four still parse.
    assert parse_traceparent(f"01-{TRACE}-00f067aa0ba902b7-01-extra") == TRACE


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "garbage",
        f"00-{TRACE}-00f067aa0ba902b7",  # no flags
        f"00-{TRACE}-00f067aa0ba902b7-01-extra",  # version 00 is exactly 55 characters
        f"ff-{TRACE}-00f067aa0ba902b7-01",  # version ff is invalid
        f"00-{'0' * 32}-00f067aa0ba902b7-01",  # all-zero trace id
        f"00-{TRACE}-{'0' * 16}-01",  # all-zero parent id
        f"00-{TRACE[:-1]}g-00f067aa0ba902b7-01",  # not hex
        f"00-{TRACE}0-0f067aa0ba902b7-01",  # wrong field lengths
    ],
)
def test_an_absent_or_malformed_traceparent_is_none(header: str | None) -> None:
    assert parse_traceparent(header) is None


async def test_a_record_lives_for_the_run_and_is_found_by_trace_id() -> None:
    runs = RunRegistry()
    request, ctx = _request("r1", TRACE, max_tokens=50)
    async with runs.register(request, ctx) as record:
        assert runs.lookup(TRACE) is record
        assert record.request_id == "r1" and record.agent == "echo"
        assert record.budget.max_tokens == 50
        assert (record.spent_input_tokens, record.spent_output_tokens, record.model_calls) == (
            0,
            0,
            0,
        )
    assert runs.lookup(TRACE) is None
    assert len(runs) == 0


async def test_a_trace_id_that_is_not_32_hex_is_keyed_by_its_sha256() -> None:
    runs = RunRegistry()
    request, ctx = _request("r1", "trace-1")
    key = hashlib.sha256(b"trace-1").hexdigest()[:32]
    async with runs.register(request, ctx) as record:
        assert record.trace_id == key == trace_id_hex("trace-1")
        assert runs.lookup(key) is record


def test_the_key_is_the_one_the_connectors_send() -> None:
    """One definition: the registry keys by `chassis.core.trace.trace_id_hex`, the id the A2A
    connectors put in the `traceparent` they send, so no private copy can drift from it.
    """
    assert not hasattr(correlation, "_trace_id_hex")
    assert vars(correlation)["trace_id_hex"] is trace_id_hex


def test_parse_traceparent_lives_in_core_and_is_re_exported() -> None:
    """`chassis.adapters.mcp` may not import `chassis.server`, so the parser is core; the old
    import path keeps working.
    """
    assert correlation.parse_traceparent is core_trace.parse_traceparent


async def test_the_record_is_removed_when_the_run_fails() -> None:
    runs = RunRegistry()
    request, ctx = _request("r1", TRACE)
    with pytest.raises(RuntimeError):
        async with runs.register(request, ctx):
            raise RuntimeError("engine fell over")
    assert runs.lookup(TRACE) is None


async def test_charge_adds_usage_and_flags_the_run_over_budget() -> None:
    runs = RunRegistry()
    request, ctx = _request("r1", TRACE, max_tokens=20)
    async with runs.register(request, ctx) as record:
        assert record.charge(Usage(input_tokens=10, output_tokens=5)) is False
        assert not record.exhausted
        assert record.charge(Usage(input_tokens=4, output_tokens=1)) is True
        assert record.exhausted
        assert record.charge(None) is True
    assert (record.spent_input_tokens, record.spent_output_tokens) == (14, 6)
    assert record.spent_tokens == 20 and record.model_calls == 3


async def test_two_concurrent_runs_never_share_a_record() -> None:
    runs = RunRegistry()
    other = "a" * 32
    both = asyncio.Event()
    seen: dict[str, RunRecord] = {}

    async def run(request_id: str, trace_id: str, tokens: int) -> None:
        request, ctx = _request(request_id, trace_id, max_tokens=20)
        async with runs.register(request, ctx) as record:
            seen[request_id] = record
            if len(seen) == 2:
                both.set()
            await both.wait()
            assert runs.lookup(trace_id) is record
            record.charge(Usage(input_tokens=tokens))
            await asyncio.sleep(0)

    await asyncio.gather(run("r1", TRACE, 25), run("r2", other, 3))
    first, second = seen["r1"], seen["r2"]
    assert first is not second
    assert first.exhausted is True and second.exhausted is False
    assert len(runs) == 0


async def test_a_second_in_flight_run_with_one_trace_id_is_refused() -> None:
    """Two runs a caller sent with the same trace id would leave the proxy unable to pick one, and
    an unpicked run has no budget. The second registration is refused while the first is in
    flight; the first keeps its record and its budget.
    """
    runs = RunRegistry()
    a, ctx_a = _request("r1", TRACE)
    b, ctx_b = _request("r2", TRACE)
    async with runs.register(a, ctx_a) as first:
        with pytest.raises(TraceIdInUse) as refused:
            async with runs.register(b, ctx_b):
                pytest.fail("the second run must not start")
        assert refused.value.trace_id == TRACE
        assert runs.lookup(TRACE) is first
        assert len(runs) == 1
    assert runs.lookup(TRACE) is None and len(runs) == 0


async def test_a_trace_id_is_free_again_once_its_run_ends() -> None:
    runs = RunRegistry()
    a, ctx_a = _request("r1", TRACE)
    b, ctx_b = _request("r2", TRACE)
    async with runs.register(a, ctx_a) as first:
        pass
    async with runs.register(b, ctx_b) as second:
        assert second is not first and runs.lookup(TRACE) is second


def test_open_and_close_are_the_register_halves_and_close_is_idempotent() -> None:
    runs = RunRegistry()
    a, ctx_a = _request("r1", TRACE)
    b, ctx_b = _request("r2", TRACE)
    record = runs.open(a, ctx_a)
    with pytest.raises(TraceIdInUse):
        runs.open(b, ctx_b)
    runs.close(record)
    runs.close(record)
    assert len(runs) == 0
    other = runs.open(b, ctx_b)
    runs.close(record)  # a stale close never frees another run's trace id
    assert runs.lookup(TRACE) is other


async def test_reserved_tokens_count_against_the_remainder() -> None:
    runs = RunRegistry()
    request, ctx = _request("r1", TRACE, max_tokens=20)
    async with runs.register(request, ctx) as record:
        record.charge(Usage(input_tokens=10, output_tokens=5))
        assert record.remaining_tokens == 5
        assert record.reserve(5) == 5
        assert record.remaining_tokens == 0 and not record.exhausted
        assert record.reserve(1000) == 0, "nothing left to reserve"
        record.settle(5, Usage(input_tokens=1, output_tokens=2))
        assert record.reserved_tokens == 0 and record.spent_tokens == 18
        assert record.remaining_tokens == 2
