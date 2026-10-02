"""PoC-2 correlation: the inbound trace id on every outbound call, and a budget per request.

Each test names the exit criterion in docs/planning/poc/002-PoC-2-two-engines-one-contract.md it
covers. No network: the workload's outbound calls are routed into the chassis app over ASGI and
recorded with their headers (`poc02_harness.route_outbound`).

An engine whose client drops the `traceparent` header is listed here as a strict xfail
(`engine_params({handle: reason})`), with the fix; none does today. The list moves to `notes/`
when the iteration closes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from chassis.fakes import ScriptedModel
from poc02_harness import (
    SIMPLIFY,
    TIMEOUT_S,
    TOOL_PROMPT,
    ToolLoopModel,
    chassis_app,
    client_for,
    engine_params,
    proxy_for,
    route_outbound,
    running,
    tool_list_failures,
)

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.mark.parametrize("engine", engine_params())
async def test_every_outbound_model_and_tool_call_carries_the_trace_id(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: every outbound model and tool call carries the inbound request's trace id,
    per engine. One `/v1/run` with a known trace id, on the tool scenario so the engine makes both
    model calls and an MCP tool call; each of them carries a `traceparent` whose trace id is
    `trace_id_hex` of the inbound one. The engine's MCP listing did not fail.
    """
    from chassis.core.trace import trace_id_hex
    from chassis.fakes.tool import default_tools

    app = chassis_app(engine, model=ToolLoopModel(), tools=default_tools())
    outbound = route_outbound(monkeypatch, app)
    failures = tool_list_failures()
    body = {"trace_id": TRACE_ID, "input": {"text": TOOL_PROMPT}}
    async with asyncio.timeout(TIMEOUT_S), running(app), client_for(app) as client:
        out = (await client.post("/v1/run", json=body)).json()
    assert out["status"] == "ok", out
    assert tool_list_failures() == failures, f"{engine}: the MCP listing failed and was swallowed"
    model_calls = outbound.to("/v1/chat/completions")
    tool_calls = outbound.to("/mcp")
    assert model_calls, "no model call reached the chassis proxy"
    assert tool_calls, "no MCP call reached the chassis tool endpoint"
    want = trace_id_hex(TRACE_ID)
    for call in model_calls + tool_calls:
        header = call.headers.get("traceparent")
        assert header, f"{engine}: {call.method} {call.path} has no traceparent"
        assert header.split("-")[1] == want, f"{engine}: {call.path} carries {header}"


def _uncorrelated(app: Any) -> int:
    telemetry = app.state.ports.telemetry
    return sum(
        value
        for (name, _), value in telemetry.counters.items()
        if name == "chassis.model_calls_uncorrelated"
    )


@pytest.mark.parametrize("engine", engine_params())
async def test_a_forwarded_traceparent_charges_every_model_call_to_its_run(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit criterion: every outbound model and tool call carries the inbound request's trace id,
    per engine (contract-v0.md, "Changes decided for v1", item 4, second case). For an engine
    that forwards `ctx["traceparent"]`, one `/v1/run` makes at least one model call through the
    chassis proxy, and the proxy charges each one to the run: `chassis.model_calls_uncorrelated`
    in the telemetry fake stays 0.
    """
    app = chassis_app(engine)
    outbound = route_outbound(monkeypatch, app)
    body = {"trace_id": TRACE_ID, "input": {"text": SIMPLIFY}}
    async with asyncio.timeout(TIMEOUT_S), running(app), client_for(app) as client:
        out = (await client.post("/v1/run", json=body)).json()
    assert out["status"] == "ok", out
    assert outbound.to("/v1/chat/completions"), "no model call reached the chassis proxy"
    assert _uncorrelated(app) == 0, f"{engine}: {_uncorrelated(app)} model calls uncorrelated"


# --- Two concurrent requests, one replica, one budget each ---

_PROBE: dict[str, Any] = {}
"""Set by the budget test: the app the probe calls, the barrier, and what each run saw."""
PROBE_CALLS = 5
"""Each model call costs 15 tokens (`ScriptedModel`'s default usage, 10 in and 5 out)."""


async def budget_probe(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """A test workload in the wire form: up to `PROBE_CALLS` model calls through the chassis
    proxy, each with the run's `traceparent`, stopping at the first refusal. It waits after its
    first call until the other run has made one too, so the two runs are in flight together.
    """
    from chassis.core.trace import trace_id_hex

    yield {"schema_version": "0", "type": "start", "request_id": ctx["request_id"]}
    traceparent = f"00-{trace_id_hex(ctx['trace_id'])}-00f067aa0ba902b7-01"
    codes: list[int] = []
    refusals: list[str] = []
    body = {"model": ctx["model_route"], "messages": [{"role": "user", "content": "probe"}]}
    transport = httpx.ASGITransport(app=_PROBE["proxy"])
    async with httpx.AsyncClient(transport=transport, base_url="http://chassis-proxy") as client:
        for i in range(PROBE_CALLS):
            r = await client.post(
                "/v1/chat/completions", json=body, headers={"traceparent": traceparent}
            )
            codes.append(r.status_code)
            if i == 0:
                await asyncio.wait_for(_PROBE["barrier"].wait(), timeout=5)
            if r.status_code != 200:
                refusals.append(r.text)
                break
    text = json.dumps({"codes": codes, "refusals": refusals})
    yield {"schema_version": "0", "type": "delta", "text": text}
    yield {"schema_version": "0", "type": "end", "status": "ok"}


async def test_two_concurrent_requests_in_one_replica_each_get_their_own_budget() -> None:
    """Exit criterion: two concurrent requests in one replica each get their own budget. Two
    runs in flight at once on one chassis: the small one (`max_tokens: 20`) is refused with 429
    `budget_exhausted` after its budget, the large one (`max_tokens: 80`) makes all five 15-token
    calls. A shared or mixed-up budget refuses the large run too.
    """
    from chassis.server.correlation import RunRegistry

    app = chassis_app(f"{__name__}:budget_probe", model=ScriptedModel())
    proxy = proxy_for(app)
    _PROBE.update(proxy=proxy, barrier=asyncio.Barrier(2))

    def run(request_id: str, trace_id: str, max_tokens: int) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "trace_id": trace_id,
            "input": {"text": "probe"},
            "budget": {"max_tokens": max_tokens, "timeout_ms": 30000},
        }

    try:
        async with (
            asyncio.timeout(TIMEOUT_S),
            running(app),
            client_for(app) as client,
        ):
            assert isinstance(app.state.runs, RunRegistry)
            small_r, large_r = await asyncio.gather(
                client.post("/v1/run", json=run("req-small", "a" * 32, 20)),
                client.post("/v1/run", json=run("req-large", "b" * 32, 80)),
            )
    finally:
        _PROBE.clear()
    small = json.loads(small_r.json()["output"]["text"])
    large = json.loads(large_r.json()["output"]["text"])
    assert small["codes"][0] == 200 and small["codes"][-1] == 429, small
    assert len(small["codes"]) < PROBE_CALLS, small
    assert "budget_exhausted" in small["refusals"][0], small
    assert large["codes"] == [200] * PROBE_CALLS, large
