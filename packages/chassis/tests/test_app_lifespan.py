"""The lifespan of `create_app`: a failed start closes the ports once and raises, and a clean
exit stops the background tasks, then the pending events, then the ports, in that order.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.feedback import InMemoryFeedback
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.ports.config import ConfigNotFound
from chassis.server import ChassisConfig, create_app
from chassis.server import app as app_module
from chassis.server.config_loader import ConfigReloader
from chassis.server.evaluator_gate import GateNotConfigured
from chassis.server.feedback import FeedbackEvents
from chassis.server.readiness import ReadinessMonitor
from chassis.server.results import ResultPublisher

AGENT = "echo"


def _config(**spec: Any) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                **spec,
            },
        }
    )


class _SetupFails(Exception):
    pass


class _CountingEngine(FakeEngine):
    """A fake engine that counts its closes and can fail its setup."""

    def __init__(self, *, fail_setup: bool = False) -> None:
        super().__init__(handle=echo)
        self.fail_setup = fail_setup
        self.closes = 0

    async def setup(self, config: Any, ports: PortBundle) -> None:
        if self.fail_setup:
            raise _SetupFails("setup")
        await super().setup(config, ports)

    async def close(self) -> None:
        self.closes += 1
        await super().close()


class _CountingModel(ScriptedModel):
    closes = 0

    async def aclose(self) -> None:
        self.closes += 1


class _CountingConfig(InMemoryConfig):
    """An `InMemoryConfig` that counts live subscriptions."""

    live = 0

    def subscribe(self, name: str, callback: Any) -> Callable[[], None]:
        unsubscribe = super().subscribe(name, callback)
        self.live += 1

        def counted() -> None:
            self.live -= 1
            unsubscribe()

        return counted


def _ports(engine: _CountingEngine, model: _CountingModel, config: _CountingConfig) -> PortBundle:
    return PortBundle(model=model, engine=engine, config=config, telemetry=InMemoryTelemetry())


FAILURES = {
    "engine setup": (
        {},
        True,
        _SetupFails,
    ),
    "first config load": (
        {"adapters": {"config": "s3"}},  # a store with no document for the agent
        False,
        ConfigNotFound,
    ),
    "with_gate": (
        {"harness": {"evaluator_gate": True}},  # the fake profile has no evaluator
        False,
        GateNotConfigured,
    ),
}


@pytest.mark.parametrize(("spec", "fail_setup", "error"), FAILURES.values(), ids=FAILURES.keys())
async def test_lifespan_closes_the_ports_once_and_raises_when_startup_fails(
    spec: dict[str, Any], fail_setup: bool, error: type[BaseException]
) -> None:
    """A failed start closes the engine and the model once, leaves no subscription, and raises."""
    engine, model, store = (
        _CountingEngine(fail_setup=fail_setup),
        _CountingModel(),
        _CountingConfig(),
    )
    app = create_app(_config(**spec), _ports(engine, model, store))

    with pytest.raises(error):
        async with app.router.lifespan_context(app):
            pass

    assert (engine.closes, model.closes) == (1, 1)
    assert store.live == 0, "the config reloader is stopped"
    assert app.state.ready is False


def _log_calls(
    monkeypatch: pytest.MonkeyPatch, log: list[str], owner: Any, name: str, label: str
) -> None:
    original = getattr(owner, name)

    async def logged(*args: Any, **kwargs: Any) -> Any:
        log.append(label)
        return await original(*args, **kwargs)

    monkeypatch.setattr(owner, name, logged)


async def test_lifespan_stops_in_the_fixed_order_when_it_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean exit stops the reloader, the probe, the publishers, then closes the ports."""
    log: list[str] = []
    for owner, name, label in [
        (ConfigReloader, "stop", "reloader"),
        (ReadinessMonitor, "stop", "monitor"),
        (ResultPublisher, "aclose", "result events"),
        (FeedbackEvents, "aclose", "feedback events"),
        (app_module, "close_ports", "ports"),
    ]:
        _log_calls(monkeypatch, log, owner, name, label)
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        events=InMemoryBus(),
        feedback=InMemoryFeedback(),
    )
    spec = {
        "adapters": {"feedback": "memory"},
        "events": {"result_events": True},
        "feedback": {"enabled": True, "events": True},
    }
    app = create_app(_config(**spec), ports)

    async with app.router.lifespan_context(app):
        assert len(app.state.pipeline.on_finished) == 2
        assert app.state.feedback_events is not None

    assert log == ["reloader", "monitor", "result events", "feedback events", "ports"]
    assert app.state.pipeline.on_finished == []
    assert app.state.feedback_events is None
    assert (app.state.ready, app.state.draining) == (False, True)
