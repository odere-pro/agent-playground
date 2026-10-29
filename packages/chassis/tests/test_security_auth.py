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
from chassis.server.security import AUTH_REFUSED, path_class, principal_of
from fastapi import FastAPI, Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

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
    app = _app()
    async with _client(app) as client:
        missing = await client.request(method, path, json=body)
        wrong = await client.request(method, path, json=body, headers=_bearer("tok-forged"))
    for response in (missing, wrong):
        assert response.status_code == 401, (path, response.text)
        assert response.headers["www-authenticate"].startswith("Bearer")
        assert "tok-forged" not in response.text
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    kind = path_class(path)
    assert telemetry.counter_value(AUTH_REFUSED, code="unauthenticated", path_class=kind) == 2


async def test_run_scope_is_needed_on_every_run_interface() -> None:
    async with _client(_app()) as client:
        for path, body in (
            ("/v1/run", RUN_BODY),
            ("/v1/chat/completions", OPENAI_BODY),
            ("/v1/messages", ANTHROPIC_BODY),
        ):
            refused = await client.post(path, json=body, headers=_bearer(READER))
            assert refused.status_code == 403, path
            assert 'scope="agent:run"' in refused.headers["www-authenticate"]
            allowed = await client.post(path, json=body, headers=_bearer(RUNNER))
            assert allowed.status_code == 200, (path, allowed.text)


async def test_read_scope_is_needed_for_the_manifest_and_docs() -> None:
    async with _client(_app()) as client:
        for path in ("/manifest", "/openapi.json"):
            assert (await client.get(path, headers=_bearer(RUNNER))).status_code == 403
            assert (await client.get(path, headers=_bearer(READER))).status_code == 200


async def test_feedback_route_needs_the_feedback_scope() -> None:
    async with _client(_app()) as client:
        response = await client.post("/v1/feedback", json={}, headers=_bearer(RUNNER))
    assert response.status_code == 403


async def test_errors_are_in_each_interface_shape() -> None:
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
    auth = StaticTokens(TOKENS)
    async with _client(_app(auth)) as client:
        auth.unavailable = True
        response = await client.post("/v1/run", json=RUN_BODY, headers=_bearer(RUNNER))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["detail"]["code"] == "auth_unavailable"


async def test_the_principal_is_on_the_request_state() -> None:
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
    """The in-process hop to `/v1/run` is checked again with the MCP caller's own token: the
    auth port sees the token twice per tool call (the MCP request, then the inner run)."""
    auth = StaticTokens(TOKENS)
    app = _app(auth)
    async with app.router.lifespan_context(app), _mcp_client(app, RUNNER) as client:
        result = await client.call_tool("echo", {"input": {"text": "hello"}})
    assert result.is_error is False, result
    assert auth.calls.count(RUNNER) >= 2
    assert None not in auth.calls, "the inner /v1/run call came without the caller's token"


async def test_mcp_without_a_token_is_401() -> None:
    app = _app()
    async with _client(app) as client:
        response = await client.post(
            MCP_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401


async def test_no_auth_changes_nothing() -> None:
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


async def test_before_the_lifespan_auth_none_passes_and_oidc_refuses() -> None:
    open_app = create_app(ChassisConfig.model_validate(CONFIG))
    named = {**CONFIG, "spec": {**CONFIG["spec"], "adapters": {"auth": "oidc"}}}
    closed_app = create_app(ChassisConfig.model_validate(named))
    for app, status in ((open_app, 200), (closed_app, 503)):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chassis"
        ) as client:
            assert (await client.get("/openapi.json")).status_code == status


async def test_the_callers_subject_is_user_id_on_the_run_span() -> None:
    """PoC-7 (ADR-007): `user.id` on `chassis.run` is the checked caller's subject, never a
    header; the session id comes from `spec.observability.session_header`."""
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
