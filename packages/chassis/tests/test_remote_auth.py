"""The remote-lane proxy listener (PoC-5 plan, section 2.3; threat model H17): the model proxy and
the tool endpoint on the pod IP, behind `BearerAuth` and `RequireRun`.

Driven with `httpx.ASGITransport`, no socket. No token and a wrong token are both 401
`remote_unauthenticated` with one body; the current and the previous token are let in; a call
must name a run in flight in its `traceparent`, else 403 `run_required`; `/dapr/*` and every
other path is 404; the token never reaches a log, a counter label, or the route.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from chassis import CHASSIS_VERSION
from chassis.core.envelope import Budget, Context, Request, TaskInput, Versions
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.remote_auth import (
    AUTH_FAILED,
    BearerAuth,
    RequireRun,
    create_remote_proxy_app,
    remote_tokens,
)
from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

CURRENT = "c" * 64  # test values, not secrets
PREVIOUS = "p" * 64
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
BODY: dict[str, Any] = {
    "model": "fake-route",
    "messages": [{"role": "user", "content": "simplify: the quick brown fox"}],
    "max_tokens": 32,
}
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
OTHER = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
UNAUTHENTICATED = {
    "error": {
        "code": "remote_unauthenticated",
        "type": "authentication_error",
        "message": "missing or invalid bearer token",
    }
}


def _app() -> tuple[FastAPI, FastAPI, PortBundle]:
    ports = PortBundle(
        model=ScriptedModel([ScriptRule(match="simplify", reply="Plain words.")]),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    public = create_app(ChassisConfig.model_validate(CONFIG), ports)
    remote = create_remote_proxy_app(public, (CURRENT, PREVIOUS))
    return public, remote, ports


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _run() -> tuple[Request, Context]:
    request = Request(
        request_id="req-remote",
        trace_id=TRACE,
        idempotency_key="idem-remote",
        agent="echo",
        agent_version="0.0.1",
        input=TaskInput(text="x"),
        budget=Budget(max_tokens=100),
    )
    ctx = Context(
        request_id=request.request_id,
        trace_id=request.trace_id,
        idempotency_key=request.idempotency_key,
        agent=request.agent,
        agent_version=request.agent_version,
        budget=request.budget,
        versions=Versions(chassis=CHASSIS_VERSION),
    )
    return request, ctx


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "traceparent": TRACEPARENT}


async def test_no_token_and_a_wrong_token_are_401_with_one_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H17 probe: no token 401, wrong token 401, the same body; counted; no header in the log."""
    public, remote, ports = _app()
    request, ctx = _run()
    caplog.set_level(logging.DEBUG)
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        missing = await client.post(
            "/v1/chat/completions", json=BODY, headers={"traceparent": TRACEPARENT}
        )
        wrong = await client.post("/v1/chat/completions", json=BODY, headers=_auth("w" * 64))
        not_bearer = await client.post(
            "/v1/chat/completions",
            json=BODY,
            headers={"Authorization": f"Basic {CURRENT}", "traceparent": TRACEPARENT},
        )
    for response in (missing, wrong, not_bearer):
        assert response.status_code == 401
        assert response.json() == UNAUTHENTICATED
        assert response.headers["www-authenticate"] == "Bearer"
    assert missing.content == wrong.content == not_bearer.content
    model = ports.model
    assert isinstance(model, ScriptedModel) and model.calls == [], "the model was never called"
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value(AUTH_FAILED, reason="missing") == 1
    assert telemetry.counter_value(AUTH_FAILED, reason="wrong") == 2
    assert "w" * 64 not in caplog.text and CURRENT not in caplog.text
    assert any("remote proxy refused" in r.getMessage() for r in caplog.records)


async def test_the_right_token_inside_a_run_is_200_and_charged_to_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H17 allowed control: the current token and the previous one (rotation), both in a run."""
    public, remote, ports = _app()
    request, ctx = _run()
    caplog.set_level(logging.DEBUG)
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx) as record,
    ):
        current = await client.post("/v1/chat/completions", json=BODY, headers=_auth(CURRENT))
        previous = await client.post("/v1/chat/completions", json=BODY, headers=_auth(PREVIOUS))
    assert current.status_code == 200, current.text
    assert previous.status_code == 200, previous.text
    assert current.json()["choices"][0]["message"]["content"] == "Plain words."
    assert record.model_calls == 2
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    assert telemetry.counter_value(AUTH_FAILED, reason="missing") == 0
    assert CURRENT not in caplog.text and PREVIOUS not in caplog.text


async def test_the_right_token_with_no_run_is_403_run_required() -> None:
    """H17: a remote holding the token cannot spend outside a run the chassis opened."""
    public, remote, ports = _app()
    request, ctx = _run()
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        no_header = await client.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {CURRENT}"}
        )
        unknown = await client.post(
            "/v1/chat/completions",
            json=BODY,
            headers={"Authorization": f"Bearer {CURRENT}", "traceparent": OTHER},
        )
        bad = await client.post(
            "/v1/chat/completions",
            json=BODY,
            headers={"Authorization": f"Bearer {CURRENT}", "traceparent": "not-a-traceparent"},
        )
        tools = await client.post(
            "/mcp", json={}, headers={"Authorization": f"Bearer {CURRENT}", "traceparent": OTHER}
        )
    for response in (no_header, unknown, bad, tools):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "run_required"
    model = ports.model
    assert isinstance(model, ScriptedModel) and model.calls == []


async def test_after_the_run_ends_its_traceparent_is_403() -> None:
    public, remote, _ = _app()
    request, ctx = _run()
    async with _client(remote) as client, public.router.lifespan_context(public):
        async with public.state.runs.register(request, ctx):
            inside = await client.post("/v1/chat/completions", json=BODY, headers=_auth(CURRENT))
        after = await client.post("/v1/chat/completions", json=BODY, headers=_auth(CURRENT))
    assert (inside.status_code, after.status_code) == (200, 403)


async def test_dapr_docs_and_other_paths_are_404_even_with_the_token() -> None:
    """The remote app is an explicit list of routes: only the model proxy and `/mcp`."""
    public, remote, _ = _app()
    request, ctx = _run()
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        for path in ("/dapr/subscribe", "/dapr/events/x", "/docs", "/openapi.json", "/v1/run"):
            response = await client.get(path, headers=_auth(CURRENT))
            assert response.status_code == 404, path
        unauthenticated = await client.get("/dapr/subscribe")
    assert unauthenticated.status_code == 401, "auth comes before routing"
    paths = {getattr(route, "path", None) for route in remote.routes}
    assert "/mcp" in paths
    assert not any(str(p).startswith("/dapr") for p in paths)


def _route_paths(routes: Any) -> set[str]:
    """Every path, with an included router's routes unfolded (FastAPI nests them)."""
    paths: set[str] = set()
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            paths |= _route_paths(inner.routes)
        else:
            paths.add(str(route.path))
    return paths


def test_the_remote_listener_route_list_is_pinned() -> None:
    """Contract v5, A.6: exactly the chat route, the Anthropic proxy route, and `/mcp`. A route
    added later fails here, on purpose."""
    _, remote, _ = _app()
    assert _route_paths(remote.routes) == {"/v1/chat/completions", "/v1/messages", "/mcp"}


ANTHROPIC_PATH = "/v1/messages"


async def test_the_anthropic_proxy_route_refusals_are_in_the_anthropic_body() -> None:
    """Contract v5, A.8: 401 and 403 on `/v1/messages` use Anthropic's error shape, and the chat
    route keeps v4's bytes. `x-api-key` alone is not a credential."""
    public, remote, _ = _app()
    request, ctx = _run()
    anthropic_body = {**BODY, "max_tokens": 32}
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        none = await client.post(ANTHROPIC_PATH, json=anthropic_body)
        key_only = await client.post(
            ANTHROPIC_PATH, json=anthropic_body, headers={"x-api-key": CURRENT}
        )
        both = await client.post(
            ANTHROPIC_PATH,
            json=anthropic_body,
            headers={"x-api-key": CURRENT, "Authorization": f"Bearer {'w' * 64}"},
        )
        no_run = await client.post(
            ANTHROPIC_PATH,
            json=anthropic_body,
            headers={"Authorization": f"Bearer {CURRENT}", "x-api-key": "k"},
        )
        chat = await client.post("/v1/chat/completions", json=BODY)
        inside = await client.post(ANTHROPIC_PATH, json=anthropic_body, headers=_auth(CURRENT))
    for response in (none, key_only, both):
        assert response.status_code == 401
        data = response.json()
        assert data["type"] == "error"
        assert data["error"] == {
            "type": "authentication_error",
            "message": "remote_unauthenticated: missing or invalid bearer token",
        }
        assert data["request_id"] == response.headers["request-id"]
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["x-should-retry"] == "false"
    assert no_run.status_code == 403
    assert no_run.json()["error"] == {
        "type": "permission_error",
        "message": "run_required: the traceparent names no run in flight",
    }
    assert chat.status_code == 401 and chat.json() == UNAUTHENTICATED
    assert chat.content == (
        b'{"error": {"code": "remote_unauthenticated", "type": "authentication_error", '
        b'"message": "missing or invalid bearer token"}}'
    )
    assert inside.status_code == 200, inside.text
    assert inside.json()["content"] == [{"type": "text", "text": "Plain words."}]


async def test_count_tokens_and_models_are_404_with_a_token_and_401_without() -> None:
    """Auth comes first. `count_tokens` is the Anthropic 404 once authenticated, but its 401 keeps
    v4's body: the middlewares test the exact path `/v1/messages` (contract v5, A.8)."""
    public, remote, _ = _app()
    request, ctx = _run()
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        for path in ("/v1/messages/count_tokens", "/v1/models"):
            allowed = await client.post(path, json={}, headers=_auth(CURRENT))
            denied = await client.post(path, json={})
            assert allowed.status_code == 404, path
            assert denied.status_code == 401 and denied.json() == UNAUTHENTICATED, path
        counted = await client.post("/v1/messages/count_tokens", json={}, headers=_auth(CURRENT))
    assert counted.json()["error"]["type"] == "not_found_error"


async def test_a_websocket_to_the_anthropic_proxy_route_is_closed_with_1008() -> None:
    _, remote, _ = _app()
    sent: list[Any] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: Any) -> None:
        sent.append(message)

    scope = {"type": "websocket", "path": ANTHROPIC_PATH, "headers": [], "query_string": b""}
    await remote(scope, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def test_the_mcp_route_is_served_inside_a_run() -> None:
    """The tool endpoint is on the remote listener too; the token lets the request reach it."""
    public, remote, _ = _app()
    request, ctx = _run()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "remote", "version": "0"},
        },
    }
    headers = {**_auth(CURRENT), "accept": "application/json, text/event-stream"}
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        response = await client.post("/mcp", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert "serverInfo" in response.text


async def test_the_authorization_header_is_stripped_before_the_route() -> None:
    seen: list[list[tuple[bytes, bytes]]] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(list(scope["headers"]))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = BearerAuth(inner, (CURRENT,), state=None)
    async with _client(app) as client:
        response = await client.get("/x", headers={"Authorization": f"Bearer {CURRENT}"})
    assert response.status_code == 204
    assert [name for name, _ in seen[0] if name.lower() == b"authorization"] == []


def test_bearer_auth_needs_a_token() -> None:
    with pytest.raises(ValueError, match="at least one token"):
        BearerAuth(lambda *_: None, (), state=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one token"):
        BearerAuth(lambda *_: None, ("",), state=None)  # type: ignore[arg-type]


def test_remote_tokens_reads_the_named_variables_and_never_shows_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = {"scheme": "bearer", "token_env": "REMOTE_TOKEN", "previous_token_env": "REMOTE_OLD"}
    monkeypatch.setenv("REMOTE_TOKEN", CURRENT)
    monkeypatch.setenv("REMOTE_OLD", PREVIOUS)
    assert remote_tokens(auth) == (CURRENT, PREVIOUS)
    monkeypatch.setenv("REMOTE_OLD", "")
    assert remote_tokens(auth) == (CURRENT,), "an empty previous token is not accepted"
    monkeypatch.delenv("REMOTE_OLD")
    assert remote_tokens(auth) == (CURRENT,)
    monkeypatch.setenv("REMOTE_TOKEN", "")
    with pytest.raises(LookupError, match="REMOTE_TOKEN") as exc:
        remote_tokens(auth)
    assert PREVIOUS not in str(exc.value)
    with pytest.raises(LookupError, match=r"spec\.engine\.auth"):
        remote_tokens(None)


class _Recorder:
    """An inner ASGI app that records every scope it is called with."""

    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope["type"])


async def _drive(app: Any, scope: Scope) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _wrapped(inner: _Recorder) -> list[Any]:
    state = type("S", (), {})()
    return [
        BearerAuth(inner, (CURRENT,), state=state),
        RequireRun(inner, state=state),
    ]


@pytest.mark.parametrize("kind", ["websocket", "webtransport", "something-else"])
async def test_non_http_scopes_are_refused_and_never_reach_the_app(kind: str) -> None:
    """F4: every scope but http and lifespan is refused; a websocket is closed with 1008."""
    for middleware_index in (0, 1):
        inner = _Recorder()
        middleware = _wrapped(inner)[middleware_index]
        scope: Scope = {"type": kind, "headers": [], "path": "/mcp"}
        if kind == "websocket":
            scope["headers"] = [(b"authorization", f"Bearer {CURRENT}".encode())]
        sent = await _drive(middleware, scope)
        assert inner.scopes == [], "the inner app was never called"
        if kind == "websocket":
            assert sent == [{"type": "websocket.close", "code": 1008}]
        else:
            assert sent == []


async def test_lifespan_scope_passes_through() -> None:
    for middleware in _wrapped(inner := _Recorder()):
        await _drive(middleware, {"type": "lifespan"})
    assert inner.scopes == ["lifespan", "lifespan"]
