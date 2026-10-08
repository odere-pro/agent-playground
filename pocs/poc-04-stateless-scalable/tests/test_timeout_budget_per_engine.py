"""PoC-4 scope item "Timeout and budget per call", on every engine (plan section 5, item 3).

Each of the four engines runs in the `sidecar` lane behind a real chassis on Unix sockets
(`poc03_harness.chassis_on_unix_sockets`): the Python echoes on the workload template server, the
TypeScript echo on Node. The model is a scripted `ModelPort` in the chassis, reached through the
chassis's model proxy, so the proxy's budget and the connector's deadline are the real ones.

- **Timeout.** The model never answers a prompt that holds `HANG`. Past `budget.timeout_ms` the run
  ends with one retryable `a2a.timeout` error, and the workload's task is cancelled: the hung model
  call is closed on the chassis side.
- **Budget.** The model proxy charges each call's usage to the run and refuses a call when nothing
  is left (429 `budget_exhausted`, counted as `chassis.model_calls_refused`). A tool loop spends the
  budget on its first call, so its second call is refused. A run whose budget is zero is refused on
  its first call.

Offline: Unix sockets only. The TypeScript cells skip when its `node_modules` is absent.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import ExitStack
from typing import Any

import httpx2
import pytest
from chassis.fakes.model import ScriptedModel, ScriptRule
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, ToolCallRequest, ToolSpec
from chassis_contracts.interface import BASE_URL, Chassis
from poc02_harness import AFTER_TOOL, TOOL_PROMPT
from poc03_harness import (
    ENGINES,
    SIMPLIFIED,
    SIMPLIFY,
    TYPESCRIPT,
    OutboundRouter,
    chassis_on_unix_sockets,
    patch_outbound,
)

LANE = "sidecar"
HANG = "simplify: HANG until the deadline"
"""suggested: a prompt the probe model never answers."""
TIMEOUT_MS = 800
"""suggested: short enough for the gate, long enough for a cold start of the run."""
CALL_TIMEOUT_S = 30.0
CLOSE_WAIT_S = 10.0
"""How long the hung model call may stay open past the deadline before the cancel counts as lost."""
BUDGET = 10
"""Less than the probe model's usage on one call (`ScriptRule` default: 10 in, 5 out)."""

ALL_ENGINES = list(ENGINES)


class ProbeModel(ScriptedModel):
    """`ScriptedModel` with the tool loop scripted, plus one prompt it never answers. Runs on the
    chassis's event loop, in another thread, so its signals are `threading.Event`s.
    """

    def __init__(self) -> None:
        super().__init__(
            [
                ScriptRule(
                    match=TOOL_PROMPT,
                    tool_call=ToolCallRequest(
                        call_id="call_1", name="glossary_lookup", arguments={"term": "SLM"}
                    ),
                ),
                ScriptRule(after_tool=True, reply=AFTER_TOOL),
                ScriptRule(match=SIMPLIFY, reply=SIMPLIFIED),
            ]
        )
        self.hung = threading.Event()
        self.closed = threading.Event()

    def _hangs(self, messages: Sequence[ModelMessage]) -> bool:
        return any(m.role == "user" and HANG in (m.content or "") for m in messages)

    async def _hang(self) -> None:
        self.hung.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed.set()

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        if self._hangs(messages):
            await self._hang()
        return await super().complete(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ModelChunk]:
        if self._hangs(messages):
            await self._hang()
        chunks = super().stream(
            messages, route=route, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        async for chunk in chunks:
            yield chunk

    def call_count(self) -> int:
        return len(self.calls)


Running = tuple[Chassis, ProbeModel]


@pytest.fixture(scope="module")
def chassis_for() -> Iterator[Callable[[str], Running]]:
    """One chassis per engine in the `sidecar` lane, each with its own probe model, kept for the
    module. Outbound HTTP from the Python workloads is routed by run to its chassis's proxy.
    """
    router = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, router.send)
    stack = ExitStack()
    started: dict[str, Running] = {}

    def get(engine: str) -> Running:
        if engine not in started:
            model = ProbeModel()
            chassis = stack.enter_context(
                chassis_on_unix_sockets(engine, LANE, model=model, router=router)
            )
            started[engine] = (chassis, model)
        return started[engine]

    try:
        yield get
    finally:
        stack.close()
        patch.undo()
    assert router.unrouted == [], f"outbound calls that named no run: {router.unrouted}"


def _refused(chassis: Chassis) -> int:
    return sum(
        value
        for (name, _), value in chassis.telemetry.counters.items()
        if name == "chassis.model_calls_refused"
    )


async def _run(chassis: Chassis, text: str, budget: dict[str, int]) -> list[dict[str, Any]]:
    """One native streamed call; its SSE events as `{"event": name, **data}`, the last one the
    `response` envelope.
    """
    body = {"input": {"text": text, "data": {}}, "budget": budget, "stream": True}
    transport = httpx2.AsyncHTTPTransport(uds=chassis.public_uds)
    async with (
        httpx2.AsyncClient(transport=transport, base_url=BASE_URL, timeout=CALL_TIMEOUT_S) as http,
        http.stream("POST", "/v1/run", json=body) as response,
    ):
        raw = await response.aread()
        assert response.status_code == 200, (response.status_code, raw)
    events: list[dict[str, Any]] = []
    for frame in raw.decode().split("\n\n"):
        lines = frame.strip().splitlines()
        name = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
        data = "".join(line[5:].strip() for line in lines if line.startswith("data:"))
        if name is not None and data:
            events.append({"event": name, **json.loads(data)})
    assert events and events[-1]["event"] == "response", events
    return events


def _error(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The run's one `error` event."""
    [error] = [e for e in events if e["event"] == "error"]
    return error


@pytest.mark.parametrize("engine", ALL_ENGINES)
async def test_past_timeout_ms_the_run_ends_with_a2a_timeout_and_the_task_is_cancelled(
    engine: str, chassis_for: Callable[[str], Running]
) -> None:
    """Scope item "Timeout and budget per call": past `budget.timeout_ms` the answer is
    `a2a.timeout` (retryable) and the workload's task is cancelled, on every engine. The cancel is
    seen where it lands: the workload's hung model call is closed in the chassis.
    """
    chassis, model = chassis_for(engine)
    model.hung.clear()
    model.closed.clear()
    started = time.monotonic()
    events = await _run(chassis, HANG, {"timeout_ms": TIMEOUT_MS})
    elapsed = time.monotonic() - started
    assert model.hung.is_set(), "the run never reached the model"
    error = _error(events)
    assert error["code"] == "a2a.timeout" and error["retryable"] is True, error
    envelope = events[-1]
    assert envelope["status"] == "error", envelope
    assert envelope["output"]["error"]["code"] == "a2a.timeout", envelope
    assert elapsed < TIMEOUT_MS / 1000 + 5, f"the deadline did not end the run: {elapsed:.1f}s"
    closed = await asyncio.to_thread(model.closed.wait, CLOSE_WAIT_S)
    assert closed, "the workload's hung model call stayed open: its task was not cancelled"


NO_TOOL_LOOP = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "echo-typescript has no tool loop: it makes one model call per run, so no run of it "
        "reaches a second call for the proxy to refuse (and budget.max_tokens must be >= 1, so "
        "the first call always has budget)"
    ),
)


@pytest.mark.parametrize(
    "engine",
    [
        pytest.param(engine, marks=NO_TOOL_LOOP) if engine == TYPESCRIPT else engine
        for engine in ALL_ENGINES
    ],
)
async def test_a_spent_budget_refuses_the_next_model_call_with_budget_exhausted(
    engine: str, chassis_for: Callable[[str], Running]
) -> None:
    """Scope item "Timeout and budget per call": `budget.max_tokens` is enforced by the model
    proxy, on every engine. The tool loop's first call spends more than the whole budget; the next
    call is refused with 429 `budget_exhausted`, never reaches the model, and ends the run with an
    error that names `budget_exhausted`.
    """
    chassis, model = chassis_for(engine)
    calls, refused = model.call_count(), _refused(chassis)
    events = await _run(chassis, TOOL_PROMPT, {"max_tokens": BUDGET})
    assert model.call_count() - calls == 1, "not exactly one call reached the model"
    assert _refused(chassis) - refused == 1, "the model proxy did not refuse exactly one call"
    error = _error(events)
    # The workloads report the proxy's refusal as its HTTP status; the proxy's own code is in the
    # message (finding, PoC-4 P9: no engine lifts `budget_exhausted` into the error code).
    assert error["code"] == "http_429" and error["retryable"] is False, error
    assert "budget_exhausted" in error["message"], error
    assert events[-1]["status"] == "error", events[-1]
