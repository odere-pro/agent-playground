"""Liveness and readiness (PoC-4 plan, section 7): `/health` never looks at the workload; `/ready`
is 503 with a `reason` while starting, while draining, and after `failures` failed probes in a
row; one good probe makes it ready again. The lifespan closes `ports.state` and `ports.events`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.events import NoEvents
from chassis.ports.state import InMemoryState
from chassis.server import ChassisConfig, create_app
from chassis.server.readiness import ProbeSettings, ReadinessMonitor

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}

FAST = ProbeSettings(interval_s=0.01, timeout_s=0.05, failures=3)


class HangingEngine(FakeEngine):
    """A workload that stops answering: `probe()` never returns while `hung` is set."""

    def __init__(self) -> None:
        super().__init__(handle=echo)
        self.hung = False

    async def probe(self) -> bool:
        self.probes += 1
        if self.hung:
            await asyncio.Event().wait()
        return True


class ClosingState(InMemoryState):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class ClosingEvents(NoEvents):
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _ports(engine: FakeEngine, **extra: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=engine,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        **extra,
    )


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


async def _wait_for(client: httpx.AsyncClient, status: int, *, tries: int = 200) -> httpx.Response:
    for _ in range(tries):
        response = await client.get("/ready")
        if response.status_code == status:
            return response
        await asyncio.sleep(0.01)
    raise AssertionError(f"/ready never answered {status}: {response.json()}")


async def test_the_monitor_turns_unhealthy_after_n_failures_and_back_after_one_success() -> None:
    engine = FakeEngine(handle=echo)
    monitor = ReadinessMonitor(engine, ProbeSettings(failures=3))
    assert monitor.healthy
    engine.healthy = False
    await monitor.check_once()
    await monitor.check_once()
    assert monitor.healthy, "two failures are not enough"
    await monitor.check_once()
    assert not monitor.healthy
    engine.healthy = True
    await monitor.check_once()
    assert monitor.healthy and monitor.failures == 0


async def test_a_probe_that_hangs_counts_as_a_failure() -> None:
    engine = HangingEngine()
    engine.hung = True
    monitor = ReadinessMonitor(engine, ProbeSettings(timeout_s=0.01, failures=1))
    await monitor.check_once()
    assert not monitor.healthy


async def test_a_probe_that_raises_counts_as_a_failure() -> None:
    class Broken(FakeEngine):
        async def probe(self) -> bool:
            raise RuntimeError("boom")

    monitor = ReadinessMonitor(Broken(handle=echo), ProbeSettings(failures=1))
    await monitor.check_once()
    assert not monitor.healthy


async def test_the_monitor_task_starts_and_stops() -> None:
    engine = FakeEngine(handle=echo)
    monitor = ReadinessMonitor(engine, FAST)
    monitor.start()
    for _ in range(100):
        if engine.probes >= 2:
            break
        await asyncio.sleep(0.01)
    await monitor.stop()
    seen = engine.probes
    await asyncio.sleep(0.05)
    assert seen >= 2 and engine.probes == seen, "the task probes, then stops"


async def test_ready_says_starting_then_ready_then_draining() -> None:
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(FakeEngine(handle=echo)))
    async with _client(app) as client:
        before = await client.get("/ready")
        assert (before.status_code, before.json()) == (
            503,
            {"status": "not ready", "reason": "starting"},
        )
        async with app.router.lifespan_context(app):
            ready = await client.get("/ready")
            assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
            app.state.draining = True
            draining = await client.get("/ready")
            assert draining.json() == {"status": "not ready", "reason": "draining"}
            assert draining.status_code == 503
            assert (await client.get("/health")).status_code == 200


async def test_ready_goes_false_after_failed_probes_and_health_stays_ok() -> None:
    engine = HangingEngine()
    app = create_app(ChassisConfig.model_validate(CONFIG), _ports(engine), probe=FAST)
    async with _client(app) as client, app.router.lifespan_context(app):
        assert (await client.get("/ready")).status_code == 200
        engine.hung = True
        down = await _wait_for(client, 503)
        assert down.json() == {"status": "not ready", "reason": "workload_unreachable"}
        assert (await client.get("/health")).json() == {"status": "ok"}
        engine.hung = False
        await _wait_for(client, 200)


async def test_the_lifespan_closes_state_and_events() -> None:
    state, events = ClosingState(), ClosingEvents()
    app = create_app(
        ChassisConfig.model_validate(CONFIG),
        _ports(FakeEngine(handle=echo), state=state, events=events),
    )
    async with app.router.lifespan_context(app):
        assert not state.closed and not events.closed
    assert state.closed and events.closed


async def test_the_sidecar_probe_is_false_on_a_bad_status_or_a_dead_workload() -> None:
    from chassis.adapters.a2a.sidecar import SidecarConnector

    connector = SidecarConnector()
    assert await connector.probe() is False, "not set up yet"

    def answer(status: int) -> httpx.AsyncClient:
        transport = httpx.MockTransport(lambda request: httpx.Response(status))
        return httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connector._probe_http = answer(200)
    assert await connector.probe() is True
    connector._probe_http = answer(503)
    assert await connector.probe() is False
    connector._probe_http = httpx.AsyncClient(
        transport=httpx.MockTransport(refuse), base_url="http://127.0.0.1:9000"
    )
    assert await connector.probe() is False
    await connector.close()
    assert connector._probe_http is None
