"""PoC-7 (022 H-6): caller auth and scopes on the public app (`chassis.server.security`).

Every public route but `/health` and `/ready` needs a valid bearer token (401 with
`WWW-Authenticate: Bearer`), the route's scope from `spec.security` (403), and an IdP that
answers (503). The bodies are each interface's own error shape. The `/v1/mcp` call reaches
`/v1/run` in process with the caller's own token, never trusted by address. Offline: httpx over
ASGI, `StaticTokens` as the auth port.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.auth import StaticTokens
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.mcp import MCP_PATH
from chassis.server.security import AUTH_REFUSED, CallerAuth, path_class, principal_of
from fastapi import FastAPI, Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.types import Receive, Scope, Send

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
RUNNER = "tok-runner"
READER = "tok-reader"
NOBODY = "tok-none"
TOKENS = {
    RUNNER: Principal("user-run", frozenset({"agent:run"})),
    READER: Principal("user-read", frozenset({"agent:read"})),
    NOBODY: Principal("user-none", frozenset()),
}
RUN_BODY = {"input": {"text": "hello"}}
OPENAI_BODY = {"model": "echo", "messages": [{"role": "user", "content": "hello"}]}
ANTHROPIC_BODY = {
    "model": "echo",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hello"}],
}


def _app(auth: StaticTokens | None = None) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        auth=auth or StaticTokens(TOKENS),
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chassis"
        ) as client,
    ):
        yield client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/health", "/ready"])
async def test_health_and_ready_need_no_token(path: str) -> None:
    """`/health` and `/ready` answer with no token."""
    async with _client(_app()) as client:
        assert (await client.get(path)).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/run", RUN_BODY),
        ("POST", "/v1/chat/completions", OPENAI_BODY),
        ("POST", "/v1/messages", ANTHROPIC_BODY),
        ("POST", MCP_PATH, {}),
        ("GET", "/manifest", None),
        ("GET", "/openapi.json", None),
        ("GET", "/docs", None),
        ("POST", "/v1/feedback", {}),
        ("GET", "/no-such-route", None),
    ],
)
async def test_every_other_route_needs_a_token(method: str, path: str, body: Any) -> None:
    """Every other route is 401 Bearer with no token or a forged one."""
    app = _app()
    async with _client(app) as client:
        missing = await client.request(method, path, json=body)
        wrong = await client.request(method, path, json=body, headers=_bearer("tok-forged"))
    refusals = [
        (r.status_code, r.headers["www-authenticate"].startswith("Bearer"), "tok-forged" in r.text)
        for r in (missing, wrong)
    ]
    assert refusals == [(401, True, False)] * 2, (missing.text, wrong.text)
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    kind = path_class(path)
    assert telemetry.counter_value(AUTH_REFUSED, code="unauthenticated", path_class=kind) == 2


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/run", RUN_BODY),
        ("/v1/chat/completions", OPENAI_BODY),
        ("/v1/messages", ANTHROPIC_BODY),
    ],
)
async def test_run_scope_is_needed_on_every_run_interface(path: str, body: Any) -> None:
    """Each run interface needs `agent:run`: 403 for a reader, 200 for a runner."""
    async with _client(_app()) as client:
        refused = await client.post(path, json=body, headers=_bearer(READER))
        allowed = await client.post(path, json=body, headers=_bearer(RUNNER))
    assert refused.status_code == 403
    assert 'scope="agent:run"' in refused.headers["www-authenticate"]
    assert allowed.status_code == 200, allowed.text


@pytest.mark.parametrize("path", ["/manifest", "/openapi.json"])
async def test_read_scope_is_needed_for_the_manifest_and_docs(path: str) -> None:
    """The manifest and docs need `agent:read`: 403 for a runner, 200 for a reader."""
    async with _client(_app()) as client:
        runner = await client.get(path, headers=_bearer(RUNNER))
        reader = await client.get(path, headers=_bearer(READER))
    assert (runner.status_code, reader.status_code) == (403, 200)


async def test_feedback_route_needs_the_feedback_scope() -> None:
    """`/v1/feedback` is 403 for a token without the feedback scope."""
    async with _client(_app()) as client:
        response = await client.post("/v1/feedback", json={}, headers=_bearer(RUNNER))
    assert response.status_code == 403


async def test_errors_are_in_each_interface_shape() -> None:
    """Auth refusals use each interface's own error body."""
    async with _client(_app()) as client:
        openai = await client.post("/v1/chat/completions", json=OPENAI_BODY)
        anthropic = await client.post("/v1/messages", json=ANTHROPIC_BODY)
        native = await client.post("/v1/run", json=RUN_BODY, headers=_bearer(NOBODY))
    assert openai.json()["error"]["type"] == "authentication_error"
    assert openai.json()["error"]["code"] == "unauthenticated"
    assert anthropic.json()["type"] == "error"
    assert anthropic.json()["error"]["type"] == "authentication_error"
    assert native.json() == {
        "detail": {
            "code": "forbidden",
            "message": "the token does not grant the scope this route needs",
        }
    }


async def test_an_idp_outage_is_503_and_retryable() -> None:
    """An IdP outage is 503 `auth_unavailable` with `Retry-After`."""
    auth = StaticTokens(TOKENS)
    async with _client(_app(auth)) as client:
        auth.unavailable = True
        response = await client.post("/v1/run", json=RUN_BODY, headers=_bearer(RUNNER))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["detail"]["code"] == "auth_unavailable"


async def test_the_principal_is_on_the_request_state() -> None:
    """The checked principal is on the request state for the route."""
    app = _app()
    seen: list[Principal | None] = []

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, str]:
        seen.append(principal_of(request.scope))
        return {"subject": request.state.principal.subject}

    async with _client(app) as client:
        response = await client.get("/whoami", headers=_bearer(NOBODY))
    assert response.json() == {"subject": "user-none"}
    assert seen == [TOKENS[NOBODY]]


async def test_a_non_bearer_header_is_no_token() -> None:
    """A non-Bearer `Authorization` header counts as no token."""
    auth = StaticTokens(TOKENS)
    async with _client(_app(auth)) as client:
        response = await client.post(
            "/v1/run", json=RUN_BODY, headers={"Authorization": f"Basic {RUNNER}"}
        )
    assert response.status_code == 401
    assert auth.calls == [None]


def _mcp_client(app: FastAPI, token: str | None) -> Client[Any]:
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
            **kw,
        )

    headers = _bearer(token) if token else None
    transport = StreamableHttpTransport(
        f"http://chassis{MCP_PATH}", headers=headers, httpx_client_factory=factory
    )
    return Client(transport)


async def test_mcp_call_carries_the_callers_token_to_v1_run() -> None:
    """The MCP tool call's inner `/v1/run` hop is checked with the caller's token."""
    auth = StaticTokens(TOKENS)
    app = _app(auth)
    async with app.router.lifespan_context(app), _mcp_client(app, RUNNER) as client:
        result = await client.call_tool("echo", {"input": {"text": "hello"}})
    assert result.is_error is False, result
    assert auth.calls.count(RUNNER) >= 2
    assert None not in auth.calls, "the inner /v1/run call came without the caller's token"


async def test_mcp_without_a_token_is_401() -> None:
    """The MCP interface is 401 with no token."""
    app = _app()
    async with _client(app) as client:
        response = await client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401


async def test_no_auth_changes_nothing() -> None:
    """With no auth port, run and manifest answer with no token."""
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(app) as client:
        assert (await client.post("/v1/run", json=RUN_BODY)).status_code == 200
        assert (await client.get("/manifest")).status_code == 200


@pytest.mark.parametrize(("auth", "status"), [(None, 200), ("oidc", 503)], ids=["none", "oidc"])
async def test_before_the_lifespan_auth_none_passes_and_oidc_refuses(
    auth: str | None, status: int
) -> None:
    """Before the lifespan, no auth passes and an `oidc` app answers 503."""
    named = (
        CONFIG
        if auth is None
        else {**CONFIG, "spec": {**CONFIG["spec"], "adapters": {"auth": auth}}}
    )
    app = create_app(ChassisConfig.model_validate(named))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://chassis"
    ) as client:
        response = await client.get("/openapi.json")
    assert response.status_code == status


async def test_the_callers_subject_is_user_id_on_the_run_span() -> None:
    """`user.id` on the run span is the token's subject, never a header (ADR-007)."""
    app = _app()
    async with _client(app) as client:
        reply = await client.post(
            "/v1/run",
            json=RUN_BODY,
            headers={**_bearer(RUNNER), "x-session-id": "s-1", "x-user-id": "spoofed"},
        )
        assert reply.status_code == 200
        telemetry: InMemoryTelemetry = app.state.ports.telemetry
    runs = [s for s in telemetry.spans if s.name == "chassis.run"]
    assert runs[-1].attributes["user.id"] == "user-run"
    assert runs[-1].attributes["session.id"] == "s-1"


async def test_auth_off_puts_no_user_id_on_the_run_span() -> None:
    """With auth off, the run span has no `user.id`."""
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    async with _client(app) as client:
        assert (await client.post("/v1/run", json=RUN_BODY)).status_code == 200
    runs = [s for s in ports.telemetry.spans if s.name == "chassis.run"]  # type: ignore[attr-defined]
    assert "user.id" not in runs[-1].attributes


class _Recorder:
    """An inner ASGI app that records the type of every scope it is called with."""

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


async def test_caller_auth_closes_with_1008_when_a_websocket_handshake_arrives() -> None:
    """R2-29: a websocket to the public app is closed with 1008 before accept."""
    inner = _Recorder()
    middleware = CallerAuth(inner, public=_app())
    scope: Scope = {"type": "websocket", "path": "/v1/run", "headers": _ws_headers()}

    sent = await _drive(middleware, scope)

    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert inner.scopes == []


@pytest.mark.parametrize("kind", ["webtransport", "something-else"])
async def test_caller_auth_drops_the_scope_when_its_type_is_not_http(kind: str) -> None:
    """R2-29: a scope type that is neither http nor lifespan never reaches the app."""
    inner = _Recorder()
    middleware = CallerAuth(inner, public=_app())

    sent = await _drive(middleware, {"type": kind, "path": "/v1/run", "headers": []})

    assert (sent, inner.scopes) == ([], [])


async def test_caller_auth_passes_the_lifespan_through_when_the_server_starts() -> None:
    """R2-29: the lifespan scope still reaches the app."""
    inner = _Recorder()
    middleware = CallerAuth(inner, public=_app())

    await _drive(middleware, {"type": "lifespan"})

    assert inner.scopes == ["lifespan"]


def _ws_headers() -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {RUNNER}".encode())]
