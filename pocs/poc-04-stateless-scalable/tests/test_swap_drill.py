"""PoC-4 exit criterion 1: the swap drill. "The same scenario tests pass with the in-memory fakes
and with the real adapters, chosen by config only."

The adapter is named in the replica's config, `spec.adapters.state` or `spec.adapters.events`,
and nowhere else: each replica's ports come from `build_ports(config.profile,
config.spec.adapters, ...)`, the call `create_app` makes when no test injects ports. No line here
picks an adapter class. Then the same scenario runs over two replicas:

- `StatePort`: a keyed call on replica 0, its repeat on replica 1 is the stored answer with
  `Idempotent-Replayed: true`, and the model is called once.
- `EventPort`: with `spec.events.result_events: true`, a run on replica 0 publishes
  `agents.task.completed.v1`, and a subscriber on replica 1's event port receives it.

`memory` runs in the gate. `valkey` and `kafka` are `network` cases (testcontainers, `make
test-integration`). An in-memory store or bus lives in one process, so for `memory` replica 1 uses
the object replica 0's config built (what one shared Valkey or broker is); a real adapter is built
per replica and shares through its server. The Dapr adapter is bound to the `EventPort` suite in
`packages/chassis/tests/integration/test_dapr_events_contract.py`; it is not repeated here, since
daprd must call back into the app (plan section 10).
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from chassis.core.results import TASK_COMPLETED
from chassis.ports.bundle import PortBundle
from chassis.ports.events import CloudEvent, EventPort
from chassis.profiles import build_ports
from chassis.server.results import key_hash
from chassis_contracts.interface import UnixApp
from poc03_harness import OutboundRouter, patch_outbound
from poc04_harness import (
    AGENT,
    SIMPLIFY,
    HarnessModel,
    Replicas,
    call,
    replica_config,
    replicas_on_unix_sockets,
)

ENGINE = {"connector": "inprocess", "handle": "echo_python:handle"}
EXPECTED = {
    ("state", "memory"): "InMemoryState",
    ("state", "valkey"): "ValkeyState",
    ("events", "memory"): "InMemoryBus",
    ("events", "kafka"): "KafkaEvents",
}
"""What each config name must build: checked, so a drill cannot pass on the wrong adapter."""
EVENT_WAIT_S = 30.0
NETWORK = pytest.mark.network


@pytest.fixture
def backing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The environment the named real adapter reads (`from_env`), from a fresh container; nothing
    for `memory`."""
    _port, adapter = request.param
    if adapter != "memory" and request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run `make test-integration`")
    if adapter == "valkey":
        from chassis_contracts.containers.valkey import valkey_container

        with valkey_container() as server:
            monkeypatch.setenv("VALKEY_URL", server.url)
            monkeypatch.setenv("VALKEY_PASSWORD", server.password)
            yield
    elif adapter == "kafka":
        from chassis_contracts.containers.kafka import kafka_container

        with kafka_container() as bootstrap:
            monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", bootstrap)
            yield
    else:
        yield


def _bundles(
    port: str, adapter: str, events_spec: dict[str, Any] | None = None
) -> list[PortBundle]:
    """Two replicas' ports, built from config the way `create_app` builds them."""
    config = replica_config(ENGINE, adapters={port: adapter}, events=events_spec)
    bundles = [
        build_ports(
            config.profile,
            config.spec.adapters,
            connector=config.spec.engine.connector,
            agent=config.agent.name,
        )
        for _ in range(2)
    ]
    for bundle in bundles:
        assert type(getattr(bundle, port)).__name__ == EXPECTED[(port, adapter)]
    return bundles


@pytest.fixture
def router() -> Iterator[OutboundRouter]:
    routed = OutboundRouter()
    patch = pytest.MonkeyPatch()
    patch_outbound(patch, routed.send)
    try:
        yield routed
    finally:
        patch.undo()


@pytest.mark.parametrize(
    ("backing", "adapter"),
    [
        (("state", "memory"), "memory"),
        pytest.param(("state", "valkey"), "valkey", marks=NETWORK),
    ],
    ids=["memory", "valkey"],
    indirect=["backing"],
)
async def test_state_port_swaps_by_config_only(
    backing: None, adapter: str, router: OutboundRouter
) -> None:
    """Exit criterion 1 (`StatePort`): with `spec.adapters.state: <adapter>`, a keyed call on
    replica 0 replays on replica 1, and the model is called once."""
    bundles = _bundles("state", adapter)
    states = [bundles[0].state, bundles[0].state if adapter == "memory" else bundles[1].state]
    with replicas_on_unix_sockets(
        2,
        "echo_python",
        "inprocess",
        model=HarnessModel(),
        router=router,
        state=states,
        adapters={"state": adapter},
    ) as replicas:
        key = f"swap-{uuid.uuid4().hex}"
        first = await call(replicas[0], "native", SIMPLIFY, key=key)
        again = await call(replicas[1], "native", SIMPLIFY, key=key)
    assert first.status == again.status == 200, (first, again)
    assert again.replayed and again.body == first.body
    assert len(replicas.model.calls) == 1


def _listener(events: EventPort, received: list[CloudEvent], got: threading.Event) -> Any:
    """An ASGI app whose lifespan subscribes to `agents.task.completed.v1` on `events`, on the
    replicas' own loop (where the port lives)."""

    async def handler(event: CloudEvent) -> None:
        received.append(event)
        got.set()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            return
        subscription = None
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                subscription = await events.subscribe(TASK_COMPLETED, handler, group="swap-drill")
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if subscription is not None:
                    await subscription.close()
                await send({"type": "lifespan.shutdown.complete"})
                return

    return app


@pytest.mark.parametrize(
    ("backing", "adapter"),
    [
        (("events", "memory"), "memory"),
        pytest.param(("events", "kafka"), "kafka", marks=NETWORK),
    ],
    ids=["memory", "kafka"],
    indirect=["backing"],
)
async def test_event_port_swaps_by_config_only(
    backing: None, adapter: str, router: OutboundRouter
) -> None:
    """Exit criterion 1 (`EventPort`): with `spec.adapters.events: <adapter>` and
    `spec.events.result_events: true`, a run on replica 0 publishes its result event, and a
    subscriber on replica 1's event port receives it, naming the run."""
    events_spec = {"result_events": True}
    bundles = _bundles("events", adapter, events_spec)
    ports = [bundles[0].events, bundles[0].events if adapter == "memory" else bundles[1].events]
    received: list[CloudEvent] = []
    got = threading.Event()
    folder = tempfile.mkdtemp(prefix="poc04-l-")
    listener = UnixApp(_listener(ports[1], received, got), str(Path(folder, "l.sock")))
    with replicas_on_unix_sockets(
        2,
        "echo_python",
        "inprocess",
        model=HarnessModel(),
        router=router,
        events=ports,
        adapters={"events": adapter},
        events_spec=events_spec,
        extra_apps=lambda _replicas: [listener],
    ) as replicas:
        _check_events_config(replicas, adapter)
        key = f"swap-{uuid.uuid4().hex}"
        first = await call(replicas[0], "native", SIMPLIFY, key=key)
        assert first.status == 200 and first.body["status"] == "ok", first
        arrived = await asyncio.to_thread(got.wait, EVENT_WAIT_S)
    assert arrived, f"no {TASK_COMPLETED} reached replica 1 in {EVENT_WAIT_S}s"
    mine = [e for e in received if e.subject == first.body["request_id"]]
    assert len(mine) == 1, received
    assert mine[0].type == TASK_COMPLETED and mine[0].idempotencykey == key_hash(key)
    assert mine[0].source.endswith(AGENT), mine[0].source


def _check_events_config(replicas: Replicas, adapter: str) -> None:
    for chassis in replicas.chassis:
        adapters = chassis.config.spec.adapters
        assert adapters is not None and adapters.events == adapter
        assert chassis.config.spec.events.result_events is True
