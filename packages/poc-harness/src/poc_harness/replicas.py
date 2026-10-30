"""N chassis replicas behind one store, for the scale and failure scenarios.

Builds on `poc_harness.interfaces` (the outbound router, the agent, the engines) and the rest
of `poc_harness` (the workload template server on a Unix socket, the TypeScript echo):

- **Replicas.** `replicas_on_unix_sockets(n, target, lane, ...)` serves N chassis, each a public
  app and a proxy app on their own Unix sockets, all on one event loop
  (`chassis_contracts.interface.serve_on_unix_sockets`). One loop matters: `InMemoryState` is
  atomic on one loop, and it stands for Valkey here. No TCP anywhere.
- **Shared ports.** `state` and `events` are one object for every replica (what a shared Valkey
  or broker is), or a sequence with one per replica (what a real adapter built per replica from
  the same config is). Defaults: one `InMemoryState`, one `InMemoryBus`.
- **One workload per replica.** In the `sidecar` lane each replica gets its own workload server
  (the template A2A server for the Python echoes, with a `RecordingTaskStore`; Node for the
  TypeScript echo). In the `inprocess` lane the handle is loaded by path in each replica.
- **One model for all.** `Replicas.engine_runs()` counts `chassis.engine.run` spans. The doubles
  are in `poc_harness.replica_doubles`, one call to a replica in `poc_harness.replica_calls`.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chassis.adapters.a2a import InProcessConnector, build_agent_card
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, default_tools
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.events import EventPort
from chassis.ports.state import InMemoryState, StatePort
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.interface import Chassis, UnixApp, serve_on_unix_sockets

from poc_harness.interfaces import AGENT, ENGINES, TYPESCRIPT, OutboundRouter
from poc_harness.replica_doubles import HarnessModel, RecordingTaskStore
from poc_harness.scenario import CHASSIS_PROXY_URL, ROUTE, SIDECAR_URL
from poc_harness.sockets import on_unix_socket
from poc_harness.typescript import typescript_echo

Engine = tuple[dict[str, Any], EngineConnector, "Workload | None"]
"""`spec.engine`, the connector that serves it, and the workload behind it, if any."""


@dataclass
class Workload:
    """One workload instance: its socket, and for the Python echoes its task store."""

    uds: str
    task_store: RecordingTaskStore | None = None


@dataclass
class Replicas:
    """The running replicas, in order, and what they share."""

    chassis: list[Chassis]
    apps: list[Any]
    workloads: list[Workload | None]
    model: HarnessModel
    states: list[StatePort]
    events: list[EventPort]

    def __getitem__(self, index: int) -> Chassis:
        return self.chassis[index]

    def engine_runs(self) -> int:
        """`chassis.engine.run` spans on every replica: one per call that reached the engine."""
        return sum(
            1
            for c in self.chassis
            for span in c.telemetry.spans
            if span.name == "chassis.engine.run"
        )

    def counter(self, name: str, **labels: Any) -> int:
        """`name` summed over every replica; with `labels`, only the series that match them."""
        total = 0
        for c in self.chassis:
            for (counter, series), value in c.telemetry.counters.items():
                if counter == name and all(dict(series).get(k) == v for k, v in labels.items()):
                    total += value
        return total


def replica_config(
    engine: Mapping[str, Any],
    *,
    adapters: Mapping[str, str] | None = None,
    idempotency: Mapping[str, Any] | None = None,
    events: Mapping[str, Any] | None = None,
) -> ChassisConfig:
    """The served agent (`poc_harness.interfaces.AGENT`), every interface on."""
    spec: dict[str, Any] = {
        "adapters": {"model": "fake", **(adapters or {})},
        "engine": dict(engine),
        "model": {"route": ROUTE},
        "prompt": {"version": "simplifier-v1"},
    }
    if idempotency is not None:
        spec["idempotency"] = dict(idempotency)
    if events is not None:
        spec["events"] = dict(events)
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc4",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": spec,
        }
    )


@contextmanager
def _python_workload(handle: str) -> Iterator[Workload]:
    from workload_a2a.server import build_app

    store = RecordingTaskStore()
    card = build_agent_card(name=handle, version="0.0.1", url=SIDECAR_URL)
    with on_unix_socket(build_app(load_handle(handle), card, task_store=store), "a2a.sock") as uds:
        yield Workload(uds, store)


def _each[T](value: T | Sequence[T] | None, n: int, default: Callable[[], T]) -> list[T]:
    """One object for every replica, or one per replica from a list or tuple."""
    if isinstance(value, list | tuple):
        if len(value) != n:
            raise ValueError(f"{len(value)} values for {n} replicas")
        return list(value)
    one: T = default() if value is None else value  # type: ignore[assignment]
    return [one] * n


@dataclass(frozen=True)
class _Build:
    """What every replica of one `replicas_on_unix_sockets` call is built from."""

    target: str
    lane: str
    model: HarnessModel
    router: OutboundRouter
    folder: str
    stack: ExitStack
    adapters: Mapping[str, str] | None
    idempotency: Mapping[str, Any] | None
    events_spec: Mapping[str, Any] | None


@dataclass(frozen=True)
class _Replica:
    """One built replica, not yet served."""

    chassis: Chassis
    public: Any
    apps: tuple[UnixApp, ...]
    workload: Workload | None


def _typescript_engine(build: _Build, index: int, proxy_uds: str) -> Engine:
    if build.lane != "sidecar":
        raise ValueError(f"{TYPESCRIPT} runs in the sidecar lane only")
    uds = str(Path(build.folder, f"t{index}.sock"))
    env = {"CHASSIS_MODEL_URL": f"{CHASSIS_PROXY_URL}/v1", "CHASSIS_MODEL_UDS": proxy_uds}
    url = build.stack.enter_context(typescript_echo(uds=uds, env=env))
    return {"connector": "sidecar", "url": url, "uds": uds}, SidecarConnector(), Workload(uds)


def _engine_for(build: _Build, index: int, proxy_uds: str) -> Engine:
    """Start what replica `index` needs for its target and lane, and say how to reach it."""
    if build.target == TYPESCRIPT:
        return _typescript_engine(build, index, proxy_uds)
    handle = ENGINES.get(build.target) or build.target
    if build.lane == "inprocess":
        return {"connector": "inprocess", "handle": handle}, InProcessConnector(), None
    if build.lane != "sidecar":
        raise ValueError(f"unknown lane {build.lane!r}")
    workload = build.stack.enter_context(_python_workload(handle))
    engine = {"connector": "sidecar", "url": SIDECAR_URL, "uds": workload.uds}
    return engine, SidecarConnector(), workload


def _replica(build: _Build, index: int, state: StatePort, events: EventPort) -> _Replica:
    """Replica `index`: its public and proxy apps, wired into the router."""
    public_uds = str(Path(build.folder, f"p{index}.sock"))
    proxy_uds = str(Path(build.folder, f"x{index}.sock"))
    engine, connector, workload = _engine_for(build, index, proxy_uds)
    config = replica_config(
        engine, adapters=build.adapters, idempotency=build.idempotency, events=build.events_spec
    )
    telemetry = InMemoryTelemetry()
    ports = PortBundle(
        model=build.model,
        engine=connector,
        config=InMemoryConfig(),
        telemetry=telemetry,
        tools=default_tools(),
        state=state,
        events=events,
    )
    public = create_app(config, ports)
    proxy = create_proxy_app(public)
    build.router.add(public, proxy_uds)
    build.stack.callback(build.router.remove, public)
    apps = (UnixApp(public, public_uds), UnixApp(proxy, proxy_uds, lifespan=False))
    return _Replica(Chassis(public_uds, telemetry, config), public, apps, workload)


@contextmanager
def replicas_on_unix_sockets(
    n: int,
    target: str,
    lane: str,
    *,
    model: HarnessModel,
    router: OutboundRouter,
    state: StatePort | Sequence[StatePort] | None = None,
    events: EventPort | Sequence[EventPort] | None = None,
    adapters: Mapping[str, str] | None = None,
    idempotency: Mapping[str, Any] | None = None,
    events_spec: Mapping[str, Any] | None = None,
    extra_apps: Callable[[Replicas], Sequence[UnixApp]] | None = None,
) -> Iterator[Replicas]:
    """`n` running replicas of `target` (an engine label or a handle path) in `lane`.

    `state` and `events`: one object shared by all, or one per replica. `adapters`,
    `idempotency`, and `events_spec` go into each replica's config as `spec.adapters`,
    `spec.idempotency`, and `spec.events`. `extra_apps` adds apps served on the same loop (a test
    listener, say), built once the replicas exist.
    """
    states: list[StatePort] = _each(state, n, InMemoryState)
    events_list: list[EventPort] = _each(events, n, InMemoryBus)
    folder = tempfile.mkdtemp(prefix="rep-")
    with ExitStack() as stack:
        stack.callback(shutil.rmtree, folder, ignore_errors=True)
        build = _Build(
            target, lane, model, router, folder, stack, adapters, idempotency, events_spec
        )
        built = [_replica(build, i, states[i], events_list[i]) for i in range(n)]
        running = Replicas(
            [r.chassis for r in built],
            [r.public for r in built],
            [r.workload for r in built],
            model,
            states,
            events_list,
        )
        apps = [app for r in built for app in r.apps]
        if extra_apps is not None:
            apps += list(extra_apps(running))
        stack.enter_context(serve_on_unix_sockets(apps))
        yield running
