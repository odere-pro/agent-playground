"""AI-generated marking (PoC-7, 023 C-5): the chassis sets `X-AI-Generated: true` and
`X-AI-Disclosure-Required: true` on every response of every run interface when
`spec.governance.marking` is on (`chassis.server.marking`).

Through the public app with `httpx.ASGITransport` and a fake engine, the MCP interface with a real
`fastmcp.Client` over `httpx2.ASGITransport`, and the `sidecar` lane over a Unix socket. No TCP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.core.events import Delta, End
from chassis.core.handle import echo, echo_wire
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.mcp import MCP_PATH
from chassis.server.marking import AI_GENERATED_HEADER, DISCLOSURE_HEADER
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import ValidationError

SPEC: dict[str, Any] = {
    "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
    "model": {"route": "fake-route"},
    "prompt": {"version": "p1"},
}

# (path, body) per run interface, complete; `stream: True` is added for the streamed call.
CALLS: list[tuple[str, dict[str, Any]]] = [
    ("/v1/run", {"input": {"text": "hi"}}),
    ("/v1/chat/completions", {"model": "echo", "messages": [{"role": "user", "content": "hi"}]}),
    (
        "/v1/messages",
        {"model": "echo", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
    ),
]


def _config(governance: dict[str, Any] | None = None, **engine: Any) -> ChassisConfig:
    spec = {**SPEC, "engine": engine or SPEC["engine"]}
    if governance is not None:
        spec["governance"] = governance
    data = {"version": "cfg-1", "profile": "fake", "agent": {"name": "echo", "version": "0.0.1"}}
    return ChassisConfig.model_validate({**data, "spec": spec})


def _app(
    governance: dict[str, Any] | None,
    engine: EngineConnector | None = None,
    config: ChassisConfig | None = None,
) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    return create_app(config or _config(governance), ports)


@asynccontextmanager
async def _serving(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
    ):
        yield client


def _flags(res: httpx.Response | httpx2.Response) -> tuple[str | None, str | None]:
    return res.headers.get(AI_GENERATED_HEADER), res.headers.get(DISCLOSURE_HEADER)


async def _run_responses(app: FastAPI) -> list[httpx.Response]:
    """One complete and one streamed call on each HTTP run interface."""
    out: list[httpx.Response] = []
    async with _serving(app) as client:
        for path, body in CALLS:
            for stream in (False, True):
                res = await client.post(path, json={**body, "stream": stream})
                assert res.status_code == 200, (path, stream, res.text)
                assert ("text/event-stream" in res.headers["content-type"]) is stream
                out.append(res)
    return out


async def _mcp_responses(app: FastAPI) -> list[httpx2.Response]:
    """Every HTTP response of an MCP session that lists the tool and calls it."""
    seen: list[httpx2.Response] = []

    async def record(response: httpx2.Response) -> None:
        seen.append(response)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        **kw: Any,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://chassis",
            headers=headers,
            timeout=timeout,
            auth=auth,
            event_hooks={"response": [record]},
            **kw,
        )

    transport = StreamableHttpTransport(f"http://chassis{MCP_PATH}", httpx_client_factory=factory)
    async with app.router.lifespan_context(app), Client(transport) as client:
        result = await client.call_tool("echo", {"input": {"text": "hi"}})
    assert result.is_error is False
    assert seen
    return seen


# One case per HTTP run interface and mode: (path, body, stream).
HTTP_TARGETS: list[tuple[str, dict[str, Any] | None, bool]] = [
    (path, body, stream) for path, body in CALLS for stream in (False, True)
]
HTTP_IDS = [f"{path}-{'stream' if stream else 'complete'}" for path, _, stream in HTTP_TARGETS]
# The MCP case (body None) covers every HTTP response of one MCP session.
TARGETS = [*HTTP_TARGETS, (MCP_PATH, None, False)]
TARGET_IDS = [*HTTP_IDS, "mcp"]
MARKED = ("true", "true")


async def _target_flags(
    app: FastAPI, target: tuple[str, dict[str, Any] | None, bool]
) -> list[tuple[str | None, str | None]]:
    """The flags of every response one target gives: one HTTP call, or one MCP session."""
    path, body, stream = target
    if body is None:
        return [_flags(r) for r in await _mcp_responses(app)]
    async with _serving(app) as client:
        res = await client.post(path, json={**body, "stream": stream})
    assert res.status_code == 200, (path, stream, res.text)
    assert ("text/event-stream" in res.headers["content-type"]) is stream
    return [_flags(res)]


@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
async def test_limited_marks_every_interface_streaming_and_complete(
    target: tuple[str, dict[str, Any] | None, bool],
) -> None:
    """A limited-risk agent marks every run interface, streamed and complete."""
    flags = await _target_flags(_app({"risk_class": "limited"}), target)
    assert flags == [MARKED] * len(flags)


@pytest.mark.parametrize("target", HTTP_TARGETS, ids=HTTP_IDS)
async def test_high_marks_too(target: tuple[str, dict[str, Any] | None, bool]) -> None:
    """A high-risk agent marks every HTTP run interface."""
    assert await _target_flags(_app({"risk_class": "high"}), target) == [MARKED]


@pytest.mark.parametrize(
    ("governance", "marked"),
    [
        ({"risk_class": "minimal", "ai_generated_marking": True}, True),
        ({"risk_class": "minimal", "ai_generated_marking": False}, False),
        ({"risk_class": "minimal"}, False),
        (None, False),
    ],
)
@pytest.mark.parametrize("target", TARGETS, ids=TARGET_IDS)
async def test_minimal_marks_only_when_asked(
    governance: Any, marked: bool, target: tuple[str, dict[str, Any] | None, bool]
) -> None:
    """A minimal-risk agent marks only when `ai_generated_marking` is true."""
    want = MARKED if marked else (None, None)
    flags = await _target_flags(_app(governance), target)
    assert flags == [want] * len(flags)


@pytest.mark.parametrize("risk_class", ["limited", "high"])
def test_marking_false_is_refused_for_limited_and_high(risk_class: str) -> None:
    """`ai_generated_marking: false` is refused for limited and high risk."""
    with pytest.raises(ValidationError, match="ai_generated_marking: false is refused"):
        _config({"risk_class": risk_class, "ai_generated_marking": False})


@pytest.mark.parametrize("path", ["/health", "/ready", "/manifest"])
async def test_health_ready_and_manifest_are_not_marked(path: str) -> None:
    """`/health`, `/ready`, and `/manifest` are never marked."""
    async with _serving(_app({"risk_class": "limited"})) as client:
        res = await client.get(path)
    assert res.status_code == 200
    assert _flags(res) == (None, None)


async def test_a_refused_call_on_a_run_route_is_marked() -> None:
    """A 422 from a run route leaves through the chassis too: it is marked."""
    async with _serving(_app({"risk_class": "limited"})) as client:
        res = await client.post("/v1/run", json={"nope": 1})
    assert res.status_code == 422
    assert _flags(res) == ("true", "true")


@pytest.mark.parametrize("target", HTTP_TARGETS, ids=HTTP_IDS)
async def test_a_workload_that_claims_not_ai_generated_is_still_marked(
    target: tuple[str, dict[str, Any] | None, bool],
) -> None:
    """A workload's `ai_generated: false` claim does not remove the marking."""
    claim = '{"ai_generated": false, "disclosure_required": false}'
    engine = FakeEngine(
        events=[Delta(text=claim), End(output={"text": claim, "ai_generated": False})]
    )
    assert await _target_flags(_app({"risk_class": "limited"}, engine), target) == [MARKED]


async def test_the_marking_follows_a_reload_of_the_config() -> None:
    """Read per request from `app.state.config`."""
    app = _app(None)
    async with _serving(app) as client:
        first = await client.post("/v1/run", json={"input": {"text": "hi"}})
        app.state.config = _config({"risk_class": "limited"})
        second = await client.post("/v1/run", json={"input": {"text": "hi"}})
    assert _flags(first) == (None, None)
    assert _flags(second) == ("true", "true")


# --- the lanes ---------------------------------------------------------------------------------


async def _lane_flags(lane: str, governance: dict[str, Any] | None) -> list[tuple[Any, Any]]:
    if lane == "inprocess":
        config = _config(governance, connector="inprocess", handle="chassis.core.handle:echo_wire")
        return [_flags(r) for r in await _run_responses(_app(None, InProcessConnector(), config))]
    card = build_agent_card(name="echo", version="0.0.1", url=SIDECAR_URL)
    async with serve_uds(build_app(echo_wire, card)) as path:
        config = _config(governance, connector="sidecar", url=SIDECAR_URL, uds=path)
        return [_flags(r) for r in await _run_responses(_app(None, SidecarConnector(), config))]


@pytest.mark.parametrize(
    ("governance", "want"),
    [
        ({"risk_class": "limited"}, ("true", "true")),
        ({"risk_class": "minimal", "ai_generated_marking": True}, ("true", "true")),
        ({"risk_class": "minimal", "ai_generated_marking": False}, (None, None)),
    ],
)
async def test_inprocess_and_sidecar_give_the_same_flags(
    governance: dict[str, Any], want: tuple[Any, Any]
) -> None:
    """The inprocess and sidecar lanes give the same marking flags."""
    inprocess = await _lane_flags("inprocess", governance)
    sidecar = await _lane_flags("sidecar", governance)
    assert inprocess == sidecar == [want] * len(inprocess)
