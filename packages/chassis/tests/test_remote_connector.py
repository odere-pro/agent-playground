"""The `remote` lane (PoC-5, plan section 2.1): `RemoteConnector` talks to a workload's A2A server
on another host with a bearer token. Here the server is the template app on uvicorn over a Unix
socket in a background task (the offline gate refuses TCP), behind a tiny ASGI wrapper that
requires the token. The token goes on the card fetch, every message, the cancel, and the probe;
the card's interface URL is replaced by the configured URL; the token is never in a log, a span,
an error, or `repr`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from a2a_uds import ASGIApp, Receive, Recorder, Scope, Send, serve_uds, serve_uds_server
from chassis.adapters.a2a.remote import RemoteConnector
from chassis.adapters.a2a.server import WireHandle, build_agent_card, build_app
from chassis.core.envelope import Budget
from chassis.core.events import End, Event, Start
from chassis.core.handle import echo_wire
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.profiles import resolve
from chassis_contracts.helpers import make_context, make_request

REMOTE_URL = "http://remote.test:8443"
"""What the config names. Over `uds` the host and port only fill the `Host` header."""
TOKEN_ENV = "POC05_TEST_REMOTE_TOKEN"
SERVER_LOGGERS = ("a2a.server", "uvicorn")
"""Loggers of the in-test server, which stands for the remote workload's process."""


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    value = secrets.token_hex(32)
    monkeypatch.setenv(TOKEN_ENV, value)
    yield value


@dataclass
class RequireBearer:
    """ASGI middleware: 401 unless `Authorization: Bearer <expected>`. `expected` may change."""

    app: ASGIApp
    expected: str

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if headers.get("authorization") != f"Bearer {self.expected}":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def _bundle(engine: Any) -> PortBundle:
    return PortBundle(
        model=ScriptedModel(), engine=engine, config=InMemoryConfig(), telemetry=InMemoryTelemetry()
    )


def _app(handle: WireHandle, *, card_url: str = REMOTE_URL) -> Any:
    return build_app(handle, build_agent_card(name="remote-test", version="1", url=card_url))


def _config(path: str | None, **extra: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "connector": "remote",
        "url": REMOTE_URL,
        "auth": {"scheme": "bearer", "token_env": TOKEN_ENV, "previous_token_env": None},
        "probe_timeout_s": 2.0,
    }
    if path is not None:
        config["uds"] = path
    config.update(extra)
    return config


async def _remote(path: str, **extra: Any) -> RemoteConnector:
    connector = RemoteConnector()
    await connector.setup(_config(path, **extra), _bundle(connector))
    return connector


async def _run(connector: RemoteConnector, request: Any) -> list[Event]:
    try:
        async with asyncio.timeout(10):
            return [e async for e in connector.run(request, make_context(request))]
    finally:
        await connector.close()


async def _stuck(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[Any]:
    yield {"type": "start", "request_id": ctx["request_id"]}
    await asyncio.Event().wait()
    yield {"type": "end"}


# --- the run ---


async def test_echo_runs_over_the_remote_lane(token: str) -> None:
    request = make_request(text="over the remote lane")
    async with serve_uds(RequireBearer(_app(echo_wire), token)) as path:
        events = await _run(await _remote(path), request)
    assert events[0] == Start(request_id=request.request_id) and events[-1] == End()


async def test_the_token_is_on_the_card_fetch_every_message_and_the_cancel(token: str) -> None:
    request = make_request().model_copy(update={"budget": Budget(timeout_ms=300)})
    recorder = Recorder(RequireBearer(_app(_stuck), token))
    async with serve_uds(recorder) as path:
        events = await _run(await _remote(path), request)
    assert events[-1].type == "error"
    assert recorder.seen[0].path == AGENT_CARD_WELL_KNOWN_PATH
    assert [s.method for s in recorder.rpc()] == ["SendStreamingMessage", "CancelTask"]
    for seen in recorder.seen:
        assert seen.headers.get("authorization") == f"Bearer {token}", seen.path


async def test_a_server_that_wants_another_token_fails_setup(token: str) -> None:
    async with serve_uds(RequireBearer(_app(echo_wire), "not-the-token")) as path:
        with pytest.raises(RuntimeError, match="not reachable"):
            await _remote(path)


# --- the card's URL is never followed ---


async def test_the_card_url_is_replaced_by_the_configured_url(token: str) -> None:
    """The card names another host; every request still goes to the configured host."""
    request = make_request()
    recorder = Recorder(RequireBearer(_app(echo_wire, card_url="http://evil.test:9999"), token))
    async with serve_uds(recorder) as path:
        connector = await _remote(path)
        client: Any = connector._client
        urls = [i.url for i in client._card.supported_interfaces]
        events = await _run(connector, request)
    assert urls == [REMOTE_URL]
    assert events[-1] == End()
    assert recorder.rpc()
    for seen in recorder.seen:
        assert seen.headers["host"] == "remote.test:8443"


async def test_the_card_url_keeps_a_configured_path(token: str) -> None:
    url = "http://remote.test:8443/agents/echo"

    async def strip_prefix(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            prefix = "/agents/echo"
            path = str(scope["path"])
            scope = dict(scope, path=path[len(prefix) :] or "/")
        await inner(scope, receive, send)

    inner = RequireBearer(_app(echo_wire, card_url="http://evil.test:9999"), token)
    recorder = Recorder(strip_prefix)
    async with serve_uds(recorder) as path:
        events = await _run(await _remote(path, url=url), make_request())
    assert events[-1] == End()
    assert recorder.seen[0].path == "/agents/echo" + AGENT_CARD_WELL_KNOWN_PATH
    assert {s.path for s in recorder.rpc()} == {"/agents/echo"}


def test_a_card_without_jsonrpc_is_refused() -> None:
    card = build_agent_card(name="x", version="1", url="http://evil.test:1")
    card.supported_interfaces[0].protocol_binding = TransportProtocol.GRPC
    connector = RemoteConnector()
    connector.url = REMOTE_URL
    with pytest.raises(ValueError, match="JSON-RPC"):
        connector._check_card(card)


def test_non_jsonrpc_interfaces_are_dropped() -> None:
    card = build_agent_card(name="x", version="1", url="http://evil.test:1")
    extra = card.supported_interfaces.add()
    extra.CopyFrom(card.supported_interfaces[0])
    extra.protocol_binding = TransportProtocol.GRPC
    connector = RemoteConnector()
    connector.url = REMOTE_URL
    connector._check_card(card)
    assert [(i.url, i.protocol_binding) for i in card.supported_interfaces] == [
        (REMOTE_URL, TransportProtocol.JSONRPC)
    ]


async def test_a_redirect_is_not_followed(token: str) -> None:
    seen: list[str] = []

    async def redirect(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(str(scope["path"]))
        location = b"http://evil.test:9999" + AGENT_CARD_WELL_KNOWN_PATH.encode()
        await send(
            {"type": "http.response.start", "status": 307, "headers": [(b"location", location)]}
        )
        await send({"type": "http.response.body", "body": b""})

    async with serve_uds(redirect) as path:
        with pytest.raises(RuntimeError, match="not reachable"):
            await _remote(path)
    assert seen == [AGENT_CARD_WELL_KNOWN_PATH]


async def test_the_clients_ignore_the_proxy_environment(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.test:3128")
    async with serve_uds(RequireBearer(_app(echo_wire), token)) as path:
        connector = await _remote(path)
        try:
            for client in (connector._http, connector._probe_http):
                assert client is not None
                assert client.trust_env is False and client.follow_redirects is False
            assert await connector.probe()
        finally:
            await connector.close()


# --- setup checks ---


async def test_a_missing_token_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(LookupError, match=TOKEN_ENV):
        await RemoteConnector().setup(_config(None), _bundle(None))


async def test_an_empty_token_variable_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "")
    with pytest.raises(LookupError, match=TOKEN_ENV):
        await RemoteConnector().setup(_config(None), _bundle(None))


@pytest.mark.parametrize(
    "auth",
    [None, {"scheme": "sigv4", "token_env": TOKEN_ENV}, {"scheme": "bearer"}],
)
async def test_auth_must_be_bearer_with_a_variable(token: str, auth: Any) -> None:
    with pytest.raises(ValueError, match=r"engine\.auth"):
        await RemoteConnector().setup(_config(None, auth=auth), _bundle(None))


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "ftp://remote.test:21",
        "http://remote.test",
        "http://user:pw@remote.test:1",
        "http://remote.test:1/?q=1",
        "http://remote.test:1/#f",
    ],
)
async def test_a_bad_url_is_refused(token: str, url: Any) -> None:
    with pytest.raises(ValueError, match=r"engine\.url"):
        await RemoteConnector().setup(_config(None, url=url), _bundle(None))


async def test_setup_against_a_dead_socket_fails_clearly(token: str, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"remote\.test:8443") as info:
        await _remote(str(tmp_path / "none.sock"))
    assert token not in str(info.value)


async def test_run_before_setup_is_an_error() -> None:
    request = make_request()
    with pytest.raises(RuntimeError, match="not set up"):
        await anext(aiter(RemoteConnector().run(request, make_context(request))))


# --- probe ---


async def test_probe_sends_the_token_and_is_true_on_200(token: str) -> None:
    recorder = Recorder(RequireBearer(_app(echo_wire), token))
    async with serve_uds(recorder) as path:
        connector = await _remote(path)
        try:
            recorder.seen.clear()
            assert await connector.probe() is True
        finally:
            await connector.close()
    [seen] = recorder.seen
    assert seen.path == AGENT_CARD_WELL_KNOWN_PATH
    assert seen.headers["authorization"] == f"Bearer {token}"


async def test_probe_is_false_on_401_and_on_a_dead_server(token: str) -> None:
    guard = RequireBearer(_app(echo_wire), token)
    async with serve_uds_server(guard) as (path, server):
        connector = await _remote(path)
        try:
            guard.expected = "rotated"
            assert await connector.probe() is False
            guard.expected = token
            assert await connector.probe() is True
            server.should_exit = True
            server.force_exit = True
            await asyncio.sleep(0.2)
            os.unlink(path)
            assert await connector.probe() is False
        finally:
            await connector.close()


async def test_probe_before_setup_is_false() -> None:
    assert await RemoteConnector().probe() is False


# --- the token never leaks ---


async def test_the_token_is_in_no_log_span_error_or_repr(
    token: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    timed_out = make_request().model_copy(update={"budget": Budget(timeout_ms=300)})
    async with serve_uds(RequireBearer(_app(_stuck), token)) as path:
        connector = await _remote(path)
        bundle: Any = connector._ports
        texts.append(repr(connector))
        texts.append(str(vars(connector)))
        events = await _run(connector, timed_out)
        texts.extend(e.model_dump_json() for e in events)
        texts.append(repr(connector))
        telemetry: InMemoryTelemetry = bundle.telemetry
        texts.extend(repr(s) for s in telemetry.spans)
        texts.extend(repr(entry) for entry in telemetry.logs)
    try:
        await _remote(str(tmp_path / "none.sock"))
    except RuntimeError as exc:
        texts.append(repr(exc))
        texts.append(repr(exc.__cause__))
    # The in-test server shares this process; in the remote lane it is the workload's process.
    # a2a-sdk's server logs every request header at DEBUG (`a2a.server.*`), which is the
    # workload's concern, not the connector's. Every other record is the chassis side.
    client_side = [r for r in caplog.records if not r.name.startswith(SERVER_LOGGERS)]
    assert client_side
    texts.extend(f"{r.name} {r.getMessage()} {r.args!r}" for r in client_side)
    assert telemetry.spans
    for text in texts:
        assert token not in text


# --- the registry ---


def test_registry_builds_the_remote_connector() -> None:
    connector = resolve("engine", "remote")
    assert isinstance(connector, RemoteConnector) and connector.kind == "remote"
