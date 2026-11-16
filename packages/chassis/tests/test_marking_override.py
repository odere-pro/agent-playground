"""023 C-5: a workload that sends its own marking still gets the chassis's.

The workload runs in the `sidecar` and `remote` lanes, the lanes where it has HTTP of its own
(`inprocess` is in `test_marking.py` and `test_marking_routes.py`). Its A2A server answers with
`X-AI-Generated: false` and `X-AI-Disclosure-Required: false`, and its events carry
`ai_marking: {"ai_generated": false, "disclosure_required": false}` in the text and in
`End.output`. On a `limited` agent the chassis sets the marking on the way out, so every HTTP run
interface, streamed and complete, carries the two headers as `true`, and the OpenAI and Anthropic
bodies carry the chassis's `ai_marking`. On native `/v1/run` and `/v1/mcp` the marking is the two
headers: the chassis does not read or rewrite `output`, so an `ai_marking` in it is the
workload's claim (narrowed by the user on 2026-11-15). Over a Unix socket and
`httpx.ASGITransport`; no TCP.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Any

import httpx
import pytest
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.server import build_agent_card, build_app
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.adapters.ai_marking import BODY_FIELD, MARKING_OPTION, with_marking
from chassis.core.events import Delta, End, Event, Start
from chassis.core.inbound import ReplyMeta
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.marking import AI_GENERATED_HEADER, DISCLOSURE_HEADER, MarkOutputs
from starlette.datastructures import State
from test_inbound import IDS as RUN_IDS
from test_inbound import SERVED
from test_marking import _mcp_responses

FALSE_MARK = {"ai_generated": False, "disclosure_required": False}
MARK = {"ai_generated": True, "disclosure_required": True}
CLAIM = json.dumps({"ai_marking": FALSE_MARK})
WORKLOAD_OUTPUT = {"text": CLAIM, "ai_marking": FALSE_MARK}
LANES = ["sidecar", "remote"]
REMOTE_URL = "http://remote.test:8443"
"""What the remote config names. Over `uds` the host and port only fill the `Host` header."""
TOKEN_ENV = "C5_TEST_REMOTE_TOKEN"

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("/v1/run", {"input": {"text": "hi"}}),
    ("/v1/chat/completions", {"model": "echo", "messages": [{"role": "user", "content": "hi"}]}),
    (
        "/v1/messages",
        {"model": "echo", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
    ),
]
TARGETS = [(path, body, stream) for path, body in CALLS for stream in (False, True)]
IDS = [f"{path}-{'stream' if stream else 'complete'}" for path, _, stream in TARGETS]

Message = MutableMapping[str, Any]
ASGI = Callable[[Message, Any, Callable[[Message], Awaitable[None]]], Awaitable[None]]


async def claims_not_ai(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Event]:
    """A wire handle that claims, in its text and its output, that it is not AI-generated."""
    yield Start(request_id=ctx["request_id"])
    yield Delta(text=CLAIM)
    yield End(output=WORKLOAD_OUTPUT)


def with_false_headers(app: ASGI) -> ASGI:
    """The workload's A2A server, sending its own `false` marking headers on every response."""

    async def wrapped(
        scope: Message, receive: Any, send: Callable[[Message], Awaitable[None]]
    ) -> None:
        async def send_false(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    *message.get("headers", ()),
                    (AI_GENERATED_HEADER.encode(), b"false"),
                    (DISCLOSURE_HEADER.encode(), b"false"),
                ]
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_false)

    return wrapped


@pytest.fixture(autouse=True)
def _remote_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, secrets.token_hex(32))


def _engine(lane: str, uds: str) -> dict[str, Any]:
    if lane == "sidecar":
        return {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds}
    auth = {"scheme": "bearer", "token_env": TOKEN_ENV, "previous_token_env": None}
    return {"connector": "remote", "url": REMOTE_URL, "uds": uds, "auth": auth}


def _connector(lane: str) -> SidecarConnector | RemoteConnector:
    return SidecarConnector() if lane == "sidecar" else RemoteConnector()


def _workload_app(lane: str) -> ASGI:
    url = SIDECAR_URL if lane == "sidecar" else REMOTE_URL
    card = build_agent_card(name="echo", version="0.0.1", url=url)
    return with_false_headers(build_app(claims_not_ai, card))


def _ports(lane: str) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=_connector(lane),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )


def _config(uds: str, lane: str = "sidecar") -> ChassisConfig:
    spec = {
        "engine": _engine(lane, uds),
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
        "governance": {"risk_class": "limited"},
    }
    data = {"version": "cfg-1", "profile": "fake", "agent": {"name": "echo", "version": "0.0.1"}}
    return ChassisConfig.model_validate({**data, "spec": spec})


def _body_marks(path: str, res: httpx.Response, stream: bool) -> list[Any]:
    """Every `ai_marking` the chassis put in an OpenAI or Anthropic body."""
    if not stream:
        return [res.json().get("ai_marking")]
    data = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: {")]
    if path == "/v1/messages":
        return [e["message"].get("ai_marking") for e in data if e["type"] == "message_start"]
    return [chunk.get("ai_marking") for chunk in data]


async def test_the_workload_server_really_sends_false_headers() -> None:
    """Guard: the sidecar this file uses does send its own `false` marking."""
    card = build_agent_card(name="echo", version="0.0.1", url=SIDECAR_URL)
    app = with_false_headers(build_app(claims_not_ai, card))
    async with serve_uds(app) as path:
        transport = httpx.AsyncHTTPTransport(uds=path)
        async with httpx.AsyncClient(transport=transport, base_url=SIDECAR_URL) as client:
            res = await client.get("/.well-known/agent-card.json")
    assert res.headers.get(AI_GENERATED_HEADER) == "false"
    assert res.headers.get(DISCLOSURE_HEADER) == "false"


def _native_output(res: httpx.Response, stream: bool) -> Any:
    """The `output` native `/v1/run` sent: the body's, or the stream's `end` event's."""
    if not stream:
        return res.json()["output"]
    data = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: {")]
    ends = [event for event in data if event.get("type") == "end"]
    assert len(ends) == 1, res.text
    return ends[0]["output"]


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize(("path", "body", "stream"), TARGETS, ids=IDS)
async def test_a_workload_that_sends_its_own_marking_still_gets_the_chassis_marking(
    path: str, body: dict[str, Any], stream: bool, lane: str
) -> None:
    """A limited agent's run interfaces carry the chassis's `true` headers, and the OpenAI and
    Anthropic bodies carry the chassis's `ai_marking`. The workload's `false` headers do not reach
    the chassis response here (the connectors do not forward them), and the workload's
    `ai_marking` sits in message content, not the top-level field; the two override cases are
    `test_mark_outputs_replaces_false_headers_already_on_the_response` and
    `test_with_marking_replaces_a_false_top_level_ai_marking`. On native `/v1/run` the marking is
    the headers: `output` is the workload's, passed through as it was sent, never rewritten."""
    async with serve_uds(_workload_app(lane)) as uds:
        app = create_app(_config(uds, lane), _ports(lane))
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        ):
            res = await client.post(path, json={**body, "stream": stream})
    assert res.status_code == 200, res.text
    assert res.headers.get_list(AI_GENERATED_HEADER) == ["true"]
    assert res.headers.get_list(DISCLOSURE_HEADER) == ["true"]
    if path == "/v1/run":
        assert _native_output(res, stream) == WORKLOAD_OUTPUT
        return
    marks = _body_marks(path, res, stream)
    assert marks
    assert marks == [MARK] * len(marks)


async def test_mark_outputs_replaces_false_headers_already_on_the_response() -> None:
    """An app under `MarkOutputs` that sends `false` flags leaves only the chassis's `true`."""

    async def inner(
        scope: Message, receive: Any, send: Callable[[Message], Awaitable[None]]
    ) -> None:
        headers = [(AI_GENERATED_HEADER.encode(), b"false"), (DISCLOSURE_HEADER.encode(), b"false")]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": b"{}"})

    state = State()
    state.config = _config("unused.sock")
    app = MarkOutputs(inner, state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
        res = await client.post("/v1/run", json={})
    assert res.headers.get_list(AI_GENERATED_HEADER) == ["true"]
    assert res.headers.get_list(DISCLOSURE_HEADER) == ["true"]


def test_with_marking_replaces_a_false_top_level_ai_marking() -> None:
    """A body that already holds `ai_marking: false` gets the chassis's flags when marking is on."""
    meta = ReplyMeta(ids=RUN_IDS, served=SERVED, created=0, options={MARKING_OPTION: True})
    body = {"id": "x", BODY_FIELD: dict(FALSE_MARK)}
    assert with_marking(body, meta)[BODY_FIELD] == MARK


@pytest.mark.parametrize("lane", LANES)
async def test_a_workload_with_its_own_marking_still_gets_true_headers_over_mcp(lane: str) -> None:
    """The agent as a tool (`/v1/mcp`): every response of the MCP session carries `true` flags."""
    async with serve_uds(_workload_app(lane)) as uds:
        responses = await _mcp_responses(create_app(_config(uds, lane), _ports(lane)))
    assert responses
    for res in responses:
        assert res.headers.get_list(AI_GENERATED_HEADER) == ["true"]
        assert res.headers.get_list(DISCLOSURE_HEADER) == ["true"]
