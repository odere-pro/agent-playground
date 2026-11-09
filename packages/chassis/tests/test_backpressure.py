"""Backpressure (CH-15): `spec.limits.max_in_flight` caps the runs in flight on one replica. Past
the cap a call is refused 503 `overloaded` with `Retry-After: 1` before the workload sees it, on
every public interface. `/ready` stays 200: overload is not a readiness failure, and the drain
(ADR-012) is untouched. The refusal counts as `chassis_requests_refused_total{reason="overloaded"}`
and the held runs show as the gauge `chassis_runs_in_flight`. The cap is in `spec.limits`, so a
reload applies to the next call.

The first tests run the public app on a real uvicorn server over a Unix socket, as
`test_shutdown.py` does, so the two held runs are real open connections. The reload test runs in
process over the ASGI transport.
"""

from __future__ import annotations

import asyncio
import copy
import tempfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from poc_harness.promtext import families, value
from proxy_fakes import run_request

MAX_IN_FLIGHT = 2
CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "adapters": {"config": "memory"},
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "limits": {"max_in_flight": MAX_IN_FLIGHT},
    },
}
HANG_GUARD_S = 30.0
RUN_BODY = {"input": {"text": "hold"}}
CHAT_BODY = {"model": "echo", "messages": [{"role": "user", "content": "hold"}]}
MESSAGES_BODY = {"model": "echo", "max_tokens": 64, "messages": [{"role": "user", "content": "x"}]}


class Held:
    """A handle that sends `start` and one delta, then waits for `release` before `end`. Counts
    the runs that reached it."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = 0

    async def __call__(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        self.started += 1
        yield Start(request_id=ctx.request_id)
        yield Delta(text="half ")
        await self.release.wait()
        yield Delta(text="done")
        yield End(status="ok")


def _ports(handle: Held) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=handle),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )


@asynccontextmanager
async def _serving(app: Any) -> AsyncIterator[str]:
    """The public app on uvicorn over a fresh Unix socket, lifespan on; yields the socket path."""
    with tempfile.TemporaryDirectory(prefix="bp-") as folder:  # short: macOS caps the path
        path = str(Path(folder, "public.sock"))
        server = uvicorn.Server(uvicorn.Config(app, uds=path, lifespan="on", log_level="warning"))
        task = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(HANG_GUARD_S):
                while not server.started:
                    if task.done():
                        task.result()
                        raise RuntimeError("uvicorn exited before it started")
                    await asyncio.sleep(0.01)
            yield path
        finally:
            server.should_exit = True
            server.force_exit = True
            await task


def _client(uds: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=uds), base_url="http://chassis", timeout=10
    )


class HeldStream:
    """One streamed `/v1/run` kept open: `lines` is its one line iterator (httpx allows one)."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.lines = response.aiter_lines()


async def _hold(stack: AsyncExitStack, client: httpx.AsyncClient) -> HeldStream:
    """Open one streamed `/v1/run` and read it up to its first delta, so the run is in flight."""
    response = await stack.enter_async_context(
        client.stream("POST", "/v1/run", json={**RUN_BODY, "stream": True})
    )
    assert response.status_code == 200, await response.aread()
    held = HeldStream(response)
    async for line in held.lines:
        if "delta" in line:
            break
    return held


async def _ends(held: HeldStream) -> None:
    """Read the held stream to its end; the last frame is the native `response` frame."""
    text = "".join([line async for line in held.lines])
    assert "end" in text and "response" in text


async def test_a_third_run_past_the_cap_is_503_overloaded_and_never_reaches_the_workload() -> None:
    """Two runs held open, a third `/v1/run` gets 503 `overloaded` with `Retry-After: 1`; the
    workload saw two runs; `/ready` is still 200; after the release a new run is served."""
    handle = Held()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(handle))
    async with _serving(app) as uds, _client(uds) as client, AsyncExitStack() as stack:
        first = await _hold(stack, client)
        second = await _hold(stack, client)
        assert handle.started == 2

        refused = await client.post("/v1/run", json=RUN_BODY)
        assert refused.status_code == 503, refused.text
        assert refused.headers["retry-after"] == "1"
        assert refused.json()["detail"]["code"] == "overloaded"
        assert handle.started == 2, "the refused run reached the workload"
        assert (await client.get("/ready")).status_code == 200

        handle.release.set()
        await _ends(first)
        await _ends(second)
        served = await client.post("/v1/run", json=RUN_BODY)
        assert served.status_code == 200, served.text
        assert handle.started == 3


async def test_the_chat_interfaces_refuse_in_their_own_shape() -> None:
    """The OpenAI interface: 503, `server_error`, `code: overloaded`, `x-should-retry: true`. The
    Anthropic interface: 503 `overloaded_error`. Both with `Retry-After: 1`, before the workload."""
    handle = Held()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(handle))
    async with _serving(app) as uds, _client(uds) as client, AsyncExitStack() as stack:
        # Kept in names: a `HeldStream` that is garbage-collected closes its connection, and a
        # client that leaves frees its run (the in-flight count then drops, as it should).
        held = [await _hold(stack, client), await _hold(stack, client)]

        openai = await client.post("/v1/chat/completions", json=CHAT_BODY)
        assert openai.status_code == 503, openai.text
        assert openai.headers["retry-after"] == "1"
        assert openai.headers["x-should-retry"] == "true"
        error = openai.json()["error"]
        assert (error["code"], error["type"], error["retryable"]) == (
            "overloaded",
            "server_error",
            True,
        )

        anthropic = await client.post("/v1/messages", json=MESSAGES_BODY)
        assert anthropic.status_code == 503, anthropic.text
        assert anthropic.headers["retry-after"] == "1"
        body = anthropic.json()["error"]
        assert body["type"] == "overloaded_error" and body["message"].startswith("overloaded: ")
        assert handle.started == 2 and len(held) == 2
        handle.release.set()


async def test_metrics_show_the_refusal_and_the_runs_in_flight() -> None:
    """`chassis_runs_in_flight` is a gauge of the held runs; the refusal is
    `chassis_requests_refused_total{reason="overloaded"}`; the gauge falls back to 0."""
    handle = Held()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(handle))
    async with _serving(app) as uds, _client(uds) as client, AsyncExitStack() as stack:
        first = await _hold(stack, client)
        second = await _hold(stack, client)
        assert (await client.post("/v1/run", json=RUN_BODY)).status_code == 503

        text = (await client.get("/metrics")).text
        assert families(text)["chassis_runs_in_flight"] == "gauge"
        assert value(text, "chassis_runs_in_flight", agent="echo") == 2
        assert value(text, "chassis_requests_refused_total", agent="echo", reason="overloaded") == 1

        handle.release.set()
        await _ends(first)
        await _ends(second)
        text = (await client.get("/metrics")).text
        assert value(text, "chassis_runs_in_flight", agent="echo") == 0


async def test_max_in_flight_reloads_with_spec_limits_and_no_restart() -> None:
    """With one run held (a registered record) and `max_in_flight: 1`, a call is refused; after
    the config store gets a document with `max_in_flight: 2`, the same call is served."""
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),  # answers at once: the served call must end
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )
    config = copy.deepcopy(CONFIG)
    config["spec"]["limits"] = {"max_in_flight": 1}
    app = create_app(ChassisConfig.model_validate(config), ports)
    transport = httpx.ASGITransport(app=app)
    request, ctx = run_request(max_tokens=100)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx),
    ):
        refused = await client.post("/v1/run", json={"input": {"text": "x"}})
        assert refused.status_code == 503 and refused.json()["detail"]["code"] == "overloaded"

        raised = copy.deepcopy(config)
        raised["version"] = "cfg-2"
        raised["spec"]["limits"] = {"max_in_flight": 2}
        assert isinstance(ports.config, InMemoryConfig)
        await ports.config.put("echo", raised)
        assert app.state.config.spec.limits.max_in_flight == 2

        served = await client.post("/v1/run", json={"input": {"text": "x"}})
        assert served.status_code == 200, served.text
