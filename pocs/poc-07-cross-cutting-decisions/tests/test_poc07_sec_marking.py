"""Exit criterion 5 (security part: AI-generated marking and chassis redaction work for every
engine with no engine-specific code).

Two engines, `echo-python` and `echo-langgraph`, each behind the chassis in the `sidecar` lane:
the workload's template A2A server on a Unix socket, its model and tool calls on the PoC-6a fake
backends (Unix sockets too). The chassis config is the same for both but for `spec.engine`. With
`risk_class: limited`, every answer of native `/v1/run`, the OpenAI interface (public port), and
the Anthropic interface (public port), streamed and complete, carries `X-AI-Generated: true` and
`X-AI-Disclosure-Required: true`; and the published result event holds `<EMAIL_ADDRESS>`, never
the address. Nothing here, and nothing in the chassis, names an engine. No TCP, no key.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.ports.state import InMemoryState
from chassis.server import ChassisConfig, create_app
from chassis.server.marking import AI_GENERATED_HEADER, DISCLOSURE_HEADER
from chassis.server.results import ResultPublisher
from poc_harness.bakeoff_tasks import ENGINES, ROUTE, SMOKE
from poc_harness.servers import a2a_on_unix_socket
from poc_harness.sidecar_lane import Backends, serve_backends, wire_python_engine

ENGINE_NAMES = ("echo-python", "echo-langgraph")
EMAIL = "jane.doe@example.com"
TIMEOUT_S = 60.0
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("/v1/run", {"input": {"text": SMOKE.text}}),
    (
        "/v1/chat/completions",
        {"model": "marked", "messages": [{"role": "user", "content": SMOKE.text}]},
    ),
    (
        "/v1/messages",
        {
            "model": "marked",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": SMOKE.text}],
        },
    ),
]


@pytest.fixture(scope="module")
def backends() -> Iterator[Backends]:
    with serve_backends() as running:
        yield running


def _config(url: str, uds: str) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc07",
            "profile": "fake",
            "agent": {"name": "marked", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "sidecar", "url": url, "uds": uds},
                "model": {"route": ROUTE},
                "events": {"result_events": True},
                "governance": {"risk_class": "limited"},
            },
        }
    )


@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
async def test_marking_and_redaction_hold_on_every_engine(
    engine_name: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5: "Each concern works on at least two engines": marking and redaction."""
    engine = ENGINES[engine_name]
    backends.reset()
    wire_python_engine(monkeypatch, engine, backends)
    bus = InMemoryBus()
    with a2a_on_unix_socket(engine.handle) as (url, uds):
        ports = PortBundle(
            model=ScriptedModel(),
            engine=SidecarConnector(),
            config=InMemoryConfig(),
            telemetry=InMemoryTelemetry(),
            state=InMemoryState(),
            events=bus,
        )
        app = create_app(_config(url, uds), ports)
        transport = httpx.ASGITransport(app=app)
        async with (
            asyncio.timeout(TIMEOUT_S),
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        ):
            flags = []
            for path, body in CALLS:
                for stream in (False, True):
                    res = await client.post(path, json={**body, "stream": stream})
                    assert res.status_code == 200, (engine_name, path, stream, res.text)
                    flags.append(
                        (
                            path,
                            stream,
                            res.headers.get(AI_GENERATED_HEADER),
                            res.headers.get(DISCLOSURE_HEADER),
                        )
                    )
            publisher = app.state.result_publisher
            assert isinstance(publisher, ResultPublisher)
            await publisher.wait(5)
            bus.published.clear()
            res = await client.post(
                "/v1/run", json={"input": {"text": SMOKE.text, "data": {"contact": EMAIL}}}
            )
            assert res.status_code == 200, res.text
            await publisher.wait(5)
    assert flags == [(p, s, "true", "true") for p, _ in CALLS for s in (False, True)]
    ((_, event),) = bus.published
    wire = json.dumps(event.model_dump(mode="json"), default=str)
    assert EMAIL not in wire
    assert "<EMAIL_ADDRESS>" in wire
