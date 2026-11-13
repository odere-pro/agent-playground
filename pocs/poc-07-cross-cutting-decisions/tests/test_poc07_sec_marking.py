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
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
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
    """The PoC-6a fake model and tool backends on Unix sockets, shared by both engines."""
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
                # `include_input`: the email is in the input, redacted on the event (019 H-17).
                "events": {"result_events": True, "include_input": True},
                "governance": {"risk_class": "limited"},
            },
        }
    )


@dataclass(frozen=True)
class MarkedRun:
    """What one engine's chassis answered: the marking headers per call, and the result event."""

    answers: dict[tuple[str, bool], tuple[int, str | None, str | None]]
    """(path, stream) -> (status code, `X-AI-Generated`, `X-AI-Disclosure-Required`)."""
    email_run_status: int
    """The status code of the run with an email in its input."""
    event_wire: list[str]
    """The JSON of each result event the run with the email published."""


def _ports(bus: InMemoryBus) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=SidecarConnector(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        events=bus,
    )


async def _answers(
    client: httpx.AsyncClient,
) -> dict[tuple[str, bool], tuple[int, str | None, str | None]]:
    """Every call of `CALLS`, complete and streamed, and its marking headers."""
    answers = {}
    for path, body in CALLS:
        for stream in (False, True):
            res = await client.post(path, json={**body, "stream": stream})
            answers[path, stream] = (
                res.status_code,
                res.headers.get(AI_GENERATED_HEADER),
                res.headers.get(DISCLOSURE_HEADER),
            )
    return answers


@pytest_asyncio.fixture(scope="module", loop_scope="module", params=ENGINE_NAMES)
async def marked(request: pytest.FixtureRequest, backends: Backends) -> MarkedRun:
    """One engine behind a `limited` chassis: every call, then one run with an email in it."""
    engine = ENGINES[request.param]
    backends.reset()
    bus = InMemoryBus()
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
        a2a_on_unix_socket(engine.handle) as (url, uds),
    ):
        wire_python_engine(monkeypatch, engine, backends)
        app = create_app(_config(url, uds), _ports(bus))
        transport = httpx.ASGITransport(app=app)
        async with (
            asyncio.timeout(TIMEOUT_S),
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        ):
            answers = await _answers(client)
            publisher = app.state.result_publisher
            if not isinstance(publisher, ResultPublisher):
                raise TypeError("result events are on, so the app has a ResultPublisher")
            await publisher.wait(5)
            bus.published.clear()
            email_run = await client.post(
                "/v1/run", json={"input": {"text": SMOKE.text, "data": {"contact": EMAIL}}}
            )
            await publisher.wait(5)
    wire = [json.dumps(e.model_dump(mode="json"), default=str) for _, e in bus.published]
    return MarkedRun(answers, email_run.status_code, wire)


CALL_IDS = [(path, stream) for path, _ in CALLS for stream in (False, True)]


@pytest.mark.parametrize(("path", "stream"), CALL_IDS)
def test_marking_and_redaction_hold_on_every_engine(
    marked: MarkedRun, path: str, stream: bool
) -> None:
    """Criterion 5: "Each concern works on at least two engines": marking and redaction.

    Each answer is marked, and the one result event of the run with an email in its input holds
    the placeholder, never the email. The tests below repeat the redaction half on its own.
    """
    assert marked.answers[path, stream] == (200, "true", "true")
    assert marked.email_run_status == 200
    (wire,) = marked.event_wire
    assert EMAIL not in wire
    assert "<EMAIL_ADDRESS>" in wire


def test_the_run_with_an_email_answers_ok(marked: MarkedRun) -> None:
    """Criterion 5: "Each concern works on at least two engines": the email run is ok."""
    assert marked.email_run_status == 200


def test_the_run_with_an_email_publishes_one_result_event(marked: MarkedRun) -> None:
    """Criterion 5: "Each concern works on at least two engines": one result event per run."""
    assert len(marked.event_wire) == 1


def test_the_result_event_never_holds_the_email(marked: MarkedRun) -> None:
    """Criterion 5: "Each concern works on at least two engines": the event drops the email."""
    assert not any(EMAIL in wire for wire in marked.event_wire)


def test_the_result_event_holds_the_email_placeholder(marked: MarkedRun) -> None:
    """Criterion 5: "Each concern works on at least two engines": the event has the placeholder."""
    assert len(marked.event_wire) == 1
    assert all("<EMAIL_ADDRESS>" in wire for wire in marked.event_wire)
