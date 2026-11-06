"""The remote-run front door: one SandboxClaim per A2A run (core review R6-11).

A fake Kubernetes API and fake remote pods on `httpx.MockTransport`; the front door is driven
through `httpx.ASGITransport` or, for a disconnect, as a bare ASGI app. No socket.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from code_runner.a2a_dispatch import (
    CANCEL_METHODS,
    STREAM_METHODS,
    TASK_NOT_FOUND,
    UNAVAILABLE_CODE,
    Engine,
    FrontDoorConfig,
    RunDispatcher,
    create_front_door,
    create_front_doors,
    load_engines,
    parse_engine_ports,
)
from code_runner.dispatch import API_GROUP_PATH, api_client, sandbox_client

NS = "poc05-remote"
TOKEN = "api-token-one"
CALLER_BEARER = "Bearer chassis-remote-token"
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
CLAIMS = f"{API_GROUP_PATH}/namespaces/{NS}/sandboxclaims"
PUBLIC = "http://remote-run-dispatch.poc05-platform.svc.cluster.local:9000"
CARD = {
    "name": "echo-claude-agent",
    "version": "0.0.1",
    "capabilities": {"streaming": True},
    "supportedInterfaces": [
        {"url": "http://pinned.invalid/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    ],
}
ENGINES = {"claude-agent": Engine("claude-agent", "remote-claude-agent", CARD)}
FAST = FrontDoorConfig(
    namespace=NS, public_url=PUBLIC, claim_timeout_s=0.5, poll_s=0.01, connect_retry_s=0.1
)
SSE = (
    b'data: {"jsonrpc":"2.0","id":"r1","result":{"task":{"id":"task-1","status":{}}}}\n\n'
    b'data: {"jsonrpc":"2.0","id":"r1","result":{"statusUpdate":{"taskId":"task-1"}}}\n\n'
)


def _stream_body(rpc_id: str = "r1") -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "method": "SendStreamingMessage", "params": {}}


class FakeApi:
    """SandboxClaims in memory; each claim gets its own pod IP. `ready` False never readies."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.claims: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.ips: dict[str, str] = {}
        self.auth: list[str | None] = []

    def live(self) -> set[str]:
        return set(self.claims) - set(self.deleted)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization"))
        assert request.url.path.startswith(CLAIMS)
        if request.method == "POST":
            claim = json.loads(request.content)
            name = claim["metadata"]["name"]
            self.claims[name] = claim
            self.created.append(claim)
            self.ips[name] = f"10.244.0.{len(self.claims) + 10}"
            return httpx.Response(201, json=claim)
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "DELETE":
            self.deleted.append(name)
            return httpx.Response(200, json={"kind": "Status"})
        claim = dict(self.claims[name])
        if self.ready:
            claim["status"] = {
                "conditions": [{"type": "Ready", "status": "True"}],
                "sandbox": {"name": name, "podIPs": [self.ips[name]]},
            }
        return httpx.Response(200, json=claim)


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], hold: asyncio.Event | None = None) -> None:
        self._chunks = chunks
        self._hold = hold
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._hold is not None:
            await self._hold.wait()

    async def aclose(self) -> None:
        self.closed = True


Answer = Callable[[httpx.Request], Awaitable[httpx.Response]]


class FakePods:
    """The claimed remote pods: records each request by host; streams `chunks` back."""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks if chunks is not None else [SSE[:40], SSE[40:]]
        self.requests: list[httpx.Request] = []
        self.hold: asyncio.Event | None = None
        self.answer: Answer | None = None
        self.streams: list[_Chunks] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self.answer is not None:
            answer = await self.answer(request)
            # As a real transport does: the raw bytes as a stream, not yet read.
            return httpx.Response(answer.status_code, headers=answer.headers, stream=answer.stream)
        stream = _Chunks(list(self.chunks), self.hold)
        self.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(TOKEN)
    return path


def _door(api: FakeApi, pods: FakePods, token_file: Path) -> RunDispatcher:
    client = api_client(
        "https://10.96.0.1:443", token_file, verify=True, transport=httpx.MockTransport(api)
    )
    upstream = sandbox_client(transport=httpx.MockTransport(pods))
    return RunDispatcher(client, upstream, FAST, ENGINES, now=lambda: NOW)


def _client(door: RunDispatcher) -> httpx.AsyncClient:
    app = create_front_door(door)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC)


async def _post(door: RunDispatcher, body: dict[str, Any]) -> httpx.Response:
    headers = {"Authorization": CALLER_BEARER, "traceparent": TRACEPARENT}
    async with _client(door) as client:
        return await client.post("/claude-agent", json=body, headers=headers)


async def test_code_runner_a2a_dispatch_card_fetch_makes_no_claim_and_points_at_the_door(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    async with _client(_door(api, pods, token_file)) as client:
        response = await client.get("/claude-agent/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert [i["url"] for i in card["supportedInterfaces"]] == [f"{PUBLIC}/claude-agent"]
    assert card["name"] == CARD["name"]
    assert api.created == [] and pods.requests == []


async def test_code_runner_a2a_dispatch_unknown_engine_is_404_and_makes_no_claim(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    async with _client(_door(api, pods, token_file)) as client:
        card = await client.get("/nope/.well-known/agent-card.json")
        run = await client.post("/nope", json=_stream_body())
    assert card.status_code == 404 and run.status_code == 404
    assert api.created == []


async def test_code_runner_a2a_dispatch_one_stream_is_one_claim_byte_for_byte_then_deleted(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    response = await _post(_door(api, pods, token_file), _stream_body())
    assert response.status_code == 200
    assert response.content == SSE
    assert response.headers["content-type"].startswith("text/event-stream")
    (claim,) = api.created
    assert claim["spec"]["warmPoolRef"] == {"name": "remote-claude-agent"}
    assert claim["spec"]["lifecycle"]["shutdownPolicy"] == "Delete"
    assert "env" not in claim["spec"] and "additionalPodMetadata" not in claim["spec"]
    assert api.deleted == [claim["metadata"]["name"]]
    (sent,) = pods.requests
    assert sent.url.host == api.ips[claim["metadata"]["name"]] and sent.url.port == 9000
    assert sent.headers["authorization"] == CALLER_BEARER
    assert sent.headers["traceparent"] == TRACEPARENT
    assert json.loads(sent.content) == _stream_body()
    # The API token goes to the API only; the caller's bearer never goes to the API.
    assert set(api.auth) == {f"Bearer {TOKEN}"}


async def test_code_runner_a2a_dispatch_two_runs_make_two_claims_on_two_pods(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    door = _door(api, pods, token_file)
    await asyncio.gather(_post(door, _stream_body("a")), _post(door, _stream_body("b")))
    assert len(api.created) == 2
    assert len({r.url.host for r in pods.requests}) == 2
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_cancel_goes_to_the_runs_pod(token_file: Path) -> None:
    api, pods = FakeApi(), FakePods()
    pods.hold = asyncio.Event()
    door = _door(api, pods, token_file)
    run = asyncio.create_task(_post(door, _stream_body()))
    for _ in range(200):
        if door.task_count():
            break
        await asyncio.sleep(0.01)
    assert door.task_count() == 1
    (run_request,) = pods.requests

    async def cancelled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "c1", "result": {"id": "task-1"}})

    pods.answer = cancelled
    cancel = {"jsonrpc": "2.0", "id": "c1", "method": "CancelTask", "params": {"id": "task-1"}}
    answer = await _post(door, cancel)
    assert answer.json()["result"] == {"id": "task-1"}
    assert pods.requests[-1].url.host == run_request.url.host
    assert len(api.created) == 1  # the cancel made no claim
    pods.hold.set()
    await run
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_cancel_of_an_unknown_task_is_task_not_found(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    body = {"jsonrpc": "2.0", "id": 7, "method": "CancelTask", "params": {"id": "ghost"}}
    answer = (await _post(_door(api, pods, token_file), body)).json()
    assert answer["id"] == 7 and answer["error"]["code"] == TASK_NOT_FOUND
    assert api.created == [] and pods.requests == []


@pytest.mark.parametrize("method", ["SendMessage", "GetTask", "tasks/get", "anything"])
async def test_code_runner_a2a_dispatch_other_method_is_an_rpc_error_without_a_claim(
    token_file: Path, method: str
) -> None:
    api, pods = FakeApi(), FakePods()
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
    answer = (await _post(_door(api, pods, token_file), body)).json()
    assert answer["error"]["code"] == -32601
    assert api.created == []


async def test_code_runner_a2a_dispatch_body_that_is_not_json_rpc_is_refused(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    async with _client(_door(api, pods, token_file)) as client:
        answer = await client.post("/claude-agent", content=b"[not json")
    assert answer.json()["error"]["code"] == -32700
    assert api.created == []


async def test_code_runner_a2a_dispatch_over_the_body_cap_is_413_without_a_claim(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    async with _client(_door(api, pods, token_file)) as client:
        answer = await client.post("/claude-agent", content=b"x" * (1024 * 1024))
    assert answer.status_code == 413
    assert api.created == []


async def test_code_runner_a2a_dispatch_claim_timeout_is_retryable_and_leaks_nothing(
    token_file: Path,
) -> None:
    api, pods = FakeApi(ready=False), FakePods()
    response = await _post(_door(api, pods, token_file), _stream_body("r9"))
    answer = response.json()
    # -32000 is not an A2A error code: the a2a-sdk raises A2AClientError, which the chassis
    # connector maps to a retryable `a2a.transport`.
    assert response.status_code == 200
    assert answer["id"] == "r9" and answer["error"]["code"] == UNAVAILABLE_CODE
    assert answer["error"]["message"].startswith("sandbox_unavailable")
    assert pods.requests == []
    assert api.live() == set() and len(api.deleted) == 1


async def test_code_runner_a2a_dispatch_upstream_refusal_never_echoes_its_body(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()

    async def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "upstream-secret-words"})

    pods.answer = refused
    response = await _post(_door(api, pods, token_file), _stream_body())
    assert "upstream-secret-words" not in response.text
    assert response.status_code == 401 and response.json() == {"error": "sandbox_refused"}
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_client_disconnect_deletes_the_claim(
    token_file: Path,
) -> None:
    api, pods = FakeApi(), FakePods()
    pods.hold = asyncio.Event()  # the pod never ends the stream
    door = _door(api, pods, token_file)
    app = create_front_door(door)
    body = json.dumps(_stream_body()).encode()
    started = asyncio.Event()
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await started.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            started.set()

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/claude-agent",
        "raw_path": b"/claude-agent",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"authorization", b"Bearer x")],
        "client": ("10.244.1.2", 5000),
        "server": ("10.96.0.50", 9000),
        "scheme": "http",
        "http_version": "1.1",
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    assert len(api.created) == 1
    assert api.live() == set()
    assert pods.streams[0].closed
    assert door.task_count() == 0


def test_code_runner_a2a_dispatch_method_sets_name_the_stream_and_cancel_only() -> None:
    assert {"SendStreamingMessage", "message/stream"} == STREAM_METHODS
    assert {"CancelTask", "tasks/cancel"} == CANCEL_METHODS


def test_code_runner_a2a_dispatch_engines_load_from_card_files(tmp_path: Path) -> None:
    (tmp_path / "claude-agent.json").write_text(json.dumps(CARD))
    (tmp_path / "smolagents.json").write_text(json.dumps(CARD))
    engines = load_engines(tmp_path, pool_prefix="remote-")
    assert {e.pool for e in engines.values()} == {"remote-claude-agent", "remote-smolagents"}
    (tmp_path / "Bad_Name.json").write_text(json.dumps(CARD))
    with pytest.raises(ValueError, match="engine"):
        load_engines(tmp_path, pool_prefix="remote-")


def test_code_runner_cli_a2a_dispatch_mode_parses() -> None:
    from code_runner import cli

    args = cli.a2a_dispatch_parser().parse_args(
        [
            "--namespace",
            NS,
            "--cards-dir",
            "/cards",
            "--public-url",
            HOST_URL,
            "--engine-port",
            "claude-agent=9001",
            "--engine-port",
            "kagent-adk=9004",
        ]
    )
    assert args.namespace == NS and args.pool_prefix == "remote-"
    assert args.engine_port == ["claude-agent=9001", "kagent-adk=9004"]


# --- one port per engine: a chassis reaches only its own engine (network policy per port) -------

HOST_URL = "http://remote-run-dispatch.poc05-platform.svc.cluster.local"
TWO = {
    "claude-agent": Engine("claude-agent", "remote-claude-agent", CARD),
    "kagent-adk": Engine("kagent-adk", "remote-kagent-adk", CARD),
}
PORTS = {"claude-agent": 9001, "kagent-adk": 9004}
PER_PORT = FrontDoorConfig(
    namespace=NS,
    public_url=HOST_URL,
    claim_timeout_s=0.5,
    poll_s=0.01,
    connect_retry_s=0.1,
    engine_ports=PORTS,
)


def _two_door(api: FakeApi, pods: FakePods, token_file: Path) -> RunDispatcher:
    client = api_client(
        "https://10.96.0.1:443", token_file, verify=True, transport=httpx.MockTransport(api)
    )
    upstream = sandbox_client(transport=httpx.MockTransport(pods))
    return RunDispatcher(client, upstream, PER_PORT, TWO, now=lambda: NOW)


def _port_client(door: RunDispatcher, port: int) -> httpx.AsyncClient:
    app = create_front_doors(door)[port]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"{HOST_URL}:{port}")


async def test_code_runner_a2a_dispatch_other_engine_on_a_port_is_404_with_no_claim(
    token_file: Path,
) -> None:
    """A chassis allowed to the claude-agent port asks for kagent-adk there: 404, nothing
    claimed, no pod reached. The paired control: the same request on kagent-adk's port runs."""
    api, pods = FakeApi(), FakePods()
    door = _two_door(api, pods, token_file)
    async with _port_client(door, 9001) as client:
        run = await client.post("/kagent-adk", json=_stream_body())
        card = await client.get("/kagent-adk/.well-known/agent-card.json")
    assert run.status_code == 404 and card.status_code == 404
    assert api.created == [] and pods.requests == []

    async with _port_client(door, 9004) as client:
        ok = await client.post("/kagent-adk", json=_stream_body())
    assert ok.status_code == 200 and ok.content == SSE
    assert [c["spec"]["warmPoolRef"]["name"] for c in api.created] == ["remote-kagent-adk"]
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_each_port_card_points_at_its_own_port(
    token_file: Path,
) -> None:
    door = _two_door(FakeApi(), FakePods(), token_file)
    for engine, port in PORTS.items():
        async with _port_client(door, port) as client:
            card = (await client.get(f"/{engine}/.well-known/agent-card.json")).json()
        assert [i["url"] for i in card["supportedInterfaces"]] == [f"{HOST_URL}:{port}/{engine}"]


async def test_code_runner_a2a_dispatch_every_port_answers_health(token_file: Path) -> None:
    door = _two_door(FakeApi(), FakePods(), token_file)
    for port in PORTS.values():
        async with _port_client(door, port) as client:
            assert (await client.get("/health")).status_code == 200


def test_code_runner_a2a_dispatch_front_doors_are_one_app_per_engine_port(
    token_file: Path,
) -> None:
    door = _two_door(FakeApi(), FakePods(), token_file)
    assert set(create_front_doors(door)) == {9001, 9004}


def test_code_runner_a2a_dispatch_engine_ports_parse_and_cover_every_engine() -> None:
    assert parse_engine_ports(["claude-agent=9001", "kagent-adk=9004"], TWO) == PORTS


@pytest.mark.parametrize(
    "specs",
    [
        ["claude-agent=9001"],  # kagent-adk has no port
        ["claude-agent=9001", "kagent-adk=9001"],  # one port, two engines
        ["claude-agent=9001", "kagent-adk=9004", "nope=9005"],  # no such engine
        ["claude-agent=9001", "claude-agent=9002", "kagent-adk=9004"],  # two ports, one engine
        ["claude-agent:9001", "kagent-adk=9004"],  # not NAME=PORT
        ["claude-agent=0", "kagent-adk=9004"],  # not a TCP port
        ["claude-agent=x", "kagent-adk=9004"],
    ],
)
def test_code_runner_a2a_dispatch_bad_engine_ports_stop_the_start(specs: list[str]) -> None:
    with pytest.raises(ValueError, match="engine port"):
        parse_engine_ports(specs, TWO)


def test_code_runner_a2a_dispatch_front_doors_need_a_port_for_every_engine(
    token_file: Path,
) -> None:
    api = api_client("https://10.96.0.1:443", token_file, verify=True)
    door = RunDispatcher(api, sandbox_client(), FAST, TWO)
    with pytest.raises(ValueError, match="engine port"):
        create_front_doors(door)


async def test_code_runner_cli_run_servers_stops_every_port_when_one_ends() -> None:
    """One process, one server per port: if one stops, the others are told to stop too, so the
    pod never keeps serving some engines and not others."""
    from code_runner.cli import run_servers

    class FakeServer:
        def __init__(self, *, ends: bool) -> None:
            self.ends = ends
            self.should_exit = False

        async def serve(self) -> None:
            # As uvicorn's main loop: a tick, then a look at should_exit (bounded for the test).
            for _ in range(500):
                if self.ends or self.should_exit:
                    return
                await asyncio.sleep(0.01)

    servers = [FakeServer(ends=False), FakeServer(ends=True), FakeServer(ends=False)]
    await asyncio.wait_for(run_servers(servers, install_signals=False), timeout=5)
    assert all(s.should_exit for s in servers)


# --- every exit path releases the claim (reviewer items a to e) --------------------------------


def _door_with(api: FakeApi, pods: FakePods, token_file: Path, **config: Any) -> RunDispatcher:
    client = api_client(
        "https://10.96.0.1:443", token_file, verify=True, transport=httpx.MockTransport(api)
    )
    upstream = sandbox_client(transport=httpx.MockTransport(pods))
    return RunDispatcher(
        client, upstream, dataclasses.replace(FAST, **config), ENGINES, now=lambda: NOW
    )


def _final_error(text: str) -> dict[str, Any]:
    """The last SSE `data:` line, parsed: the door's own JSON-RPC error after a lost run."""
    lines = [ln for ln in text.splitlines() if ln.startswith("data:")]
    return dict(json.loads(lines[-1][len("data:") :]))


async def _wait_for_run(door: RunDispatcher) -> None:
    for _ in range(200):
        if door.task_count():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the run never started")


@pytest.mark.parametrize("status", [401, 403])
async def test_code_runner_a2a_dispatch_pod_auth_refusal_is_the_same_http_status(
    token_file: Path, status: int
) -> None:
    """A pod 401 or 403 before the stream: the same status, a fixed body (never the pod's), and
    the claim released. The chassis sees what it saw before the door existed."""
    api, pods = FakeApi(), FakePods()

    async def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "pod-words"})

    pods.answer = refused
    response = await _post(_door(api, pods, token_file), _stream_body())
    assert response.status_code == status
    assert response.json() == {"error": "sandbox_refused"}
    assert api.live() == set() and len(api.deleted) == 1


async def test_code_runner_a2a_dispatch_pod_5xx_stays_the_retryable_rpc_error(
    token_file: Path,
) -> None:
    """The control: any other refusal is still 200 with the retryable -32000."""
    api, pods = FakeApi(), FakePods()

    async def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "busy"})

    pods.answer = refused
    response = await _post(_door(api, pods, token_file), _stream_body())
    assert response.status_code == 200
    assert response.json()["error"]["code"] == UNAVAILABLE_CODE
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_refusal_whose_close_fails_still_releases(
    token_file: Path,
) -> None:
    """(a) `aclose` raising on a refused upstream must not skip the release."""
    api, pods = FakeApi(), FakePods()

    class BadClose(_Chunks):
        async def aclose(self) -> None:
            raise httpx.ReadError("close failed")

    async def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=BadClose([b"x"]))

    pods.answer = refused
    door = _door(api, pods, token_file)
    with pytest.raises(httpx.ReadError):
        await _post(door, _stream_body())
    assert api.live() == set() and len(api.deleted) == 1
    assert door._slots._value == FAST.max_claims


async def test_code_runner_a2a_dispatch_pod_that_never_sends_headers_frees_the_claim(
    token_file: Path,
) -> None:
    """(b) A pod that accepts the connection and never answers: the header timeout ends it."""
    api, pods = FakeApi(), FakePods()
    never = asyncio.Event()

    async def silent(request: httpx.Request) -> httpx.Response:
        await never.wait()
        raise AssertionError("unreachable")

    pods.answer = silent
    door = _door_with(api, pods, token_file, headers_timeout_s=0.1)
    answer = (await asyncio.wait_for(_post(door, _stream_body()), timeout=5)).json()
    assert answer["error"]["code"] == UNAVAILABLE_CODE
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_cancel_that_hangs_times_out(token_file: Path) -> None:
    """(b) The cancel has its own short timeout; the run it targets is untouched."""
    api, pods = FakeApi(), FakePods()
    pods.hold = asyncio.Event()
    door = _door_with(api, pods, token_file, cancel_timeout_s=0.1)
    run = asyncio.create_task(_post(door, _stream_body()))
    await _wait_for_run(door)
    never = asyncio.Event()

    async def silent(request: httpx.Request) -> httpx.Response:
        await never.wait()
        raise AssertionError("unreachable")

    pods.answer = silent
    cancel = {"jsonrpc": "2.0", "id": "c1", "method": "CancelTask", "params": {"id": "task-1"}}
    answer = (await asyncio.wait_for(_post(door, cancel), timeout=5)).json()
    assert answer["error"]["code"] == UNAVAILABLE_CODE
    pods.hold.set()
    await run
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_run_deadline_ends_with_an_error_line_and_frees(
    token_file: Path,
) -> None:
    """(c) The run deadline: the stream ends with the door's own retryable JSON-RPC error."""
    api, pods = FakeApi(), FakePods([SSE[:40]])  # half an event, then silence
    pods.hold = asyncio.Event()
    door = _door_with(api, pods, token_file, run_deadline_s=0.2)
    response = await asyncio.wait_for(_post(door, _stream_body()), timeout=5)
    error = _final_error(response.text)
    assert error["id"] == "r1" and error["error"]["code"] == UNAVAILABLE_CODE
    assert error["error"]["message"].startswith("sandbox_lost")
    assert api.live() == set() and door.task_count() == 0


async def test_code_runner_a2a_dispatch_lost_upstream_mid_stream_ends_with_an_error_line(
    token_file: Path,
) -> None:
    """(c) The pod's stream breaks after one event: the caller gets that event, then the error."""
    api, pods = FakeApi(), FakePods()

    class Breaks(_Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield SSE.split(b"\n\n")[0] + b"\n\n"
            raise httpx.ReadError("pod went away")

    async def breaking(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Breaks([]))

    pods.answer = breaking
    door = _door(api, pods, token_file)
    response = await asyncio.wait_for(_post(door, _stream_body()), timeout=5)
    assert response.text.startswith(SSE.split(b"\n\n")[0].decode())
    assert _final_error(response.text)["error"]["code"] == UNAVAILABLE_CODE
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_no_free_slot_is_retryable_and_claims_nothing(
    token_file: Path,
) -> None:
    """max_claims exhausted: the next run waits claim_timeout_s, then -32000, no claim made."""
    api, pods = FakeApi(), FakePods()
    pods.hold = asyncio.Event()
    door = _door_with(api, pods, token_file, max_claims=1, claim_timeout_s=0.1)
    run = asyncio.create_task(_post(door, _stream_body()))
    await _wait_for_run(door)
    answer = (await _post(door, _stream_body("r2"))).json()
    assert answer["error"]["code"] == UNAVAILABLE_CODE
    assert "slot" in answer["error"]["message"]
    assert len(api.created) == 1
    pods.hold.set()
    await run
    assert api.live() == set()


async def test_code_runner_a2a_dispatch_asks_the_pod_for_no_encoding_and_drops_hop_headers(
    token_file: Path,
) -> None:
    """The pod is asked for `identity`, whatever the chassis sent, and a header the `Connection`
    header names is hop-by-hop, so it is not forwarded."""
    api, pods = FakeApi(), FakePods()
    headers = {
        "Authorization": CALLER_BEARER,
        "Accept-Encoding": "gzip, br",
        "Connection": "x-hop",
        "X-Hop": "1",
    }
    async with _client(_door(api, pods, token_file)) as client:
        response = await client.post("/claude-agent", json=_stream_body(), headers=headers)
    assert response.status_code == 200
    (sent,) = pods.requests
    assert sent.headers["accept-encoding"] == "identity"
    assert "x-hop" not in sent.headers
    assert sent.headers["authorization"] == CALLER_BEARER


async def test_code_runner_a2a_dispatch_encoded_pod_stream_is_retryable_and_frees(
    token_file: Path,
) -> None:
    """A pod that encodes its stream anyway: the door cannot scan it for the task id, and the
    chassis caps no decoded bytes, so the run is the retryable error and the claim is freed."""
    api, pods = FakeApi(), FakePods()

    async def encoded(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
            content=b"\x1f\x8b-not-read",
        )

    pods.answer = encoded
    response = await _post(_door(api, pods, token_file), _stream_body())
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == UNAVAILABLE_CODE
    assert "not-read" not in response.text
    assert api.live() == set() and len(api.deleted) == 1
