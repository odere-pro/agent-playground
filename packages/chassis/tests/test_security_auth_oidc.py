"""022 H-6: the public app's caller auth with `OidcAuth` as the auth port (OIDC only in v1).

`test_security_auth.py` runs the routes behind `StaticTokens`; `test_oidc_adapter.py` checks
`OidcAuth` at the port. This file puts `OidcAuth` in front of every run and read route: no bearer,
or a wrong OIDC bearer, is 401 `WWW-Authenticate: Bearer`, and the refusal never echoes the token.
The paired control: a valid OIDC token with the route's scope gets 200 on the same routes, so the
401 comes from the token and not from the wiring. The `/v1/mcp` hop to `/v1/run` is checked again
with the caller's own JWT.

Offline: the IdP is a generated RSA key and its JWKS behind an httpx `MockTransport`
(`oidc_idp.Idp`), and the app runs over `httpx.ASGITransport`. No socket, no key.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
import jwt
import pytest
from chassis.adapters.oidc import OidcAuth
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.auth import Principal
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.interfaces.mcp import MCP_PATH
from chassis.server.security import (
    AUTH_REFUSED,
    OPEN_PATHS,
    READ_PATHS,
    RUN_PATHS,
    path_class,
)
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from oidc_idp import AUDIENCE, ISSUER, JWKS_URL, Idp, new_rsa

CONFIG: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
RUN_BODY = {"input": {"text": "hello"}}
OPENAI_BODY = {"model": "echo", "messages": [{"role": "user", "content": "hello"}]}
ANTHROPIC_BODY = {
    "model": "echo",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hello"}],
}
RUN_BODIES: dict[str, Any] = {
    "/v1/run": RUN_BODY,
    "/v1/chat/completions": OPENAI_BODY,
    "/v1/messages": ANTHROPIC_BODY,
    MCP_PATH: {},
}
"""A body per run path. A new path in `RUN_PATHS` without one fails at collection (KeyError)."""
RUN_ROUTES: list[tuple[str, str, Any]] = [("POST", p, RUN_BODIES[p]) for p in sorted(RUN_PATHS)]
READ_ROUTES: list[tuple[str, str, Any]] = [("GET", p, None) for p in sorted(READ_PATHS)]
GUARDED = RUN_PATHS | READ_PATHS
"""The run and read routes, from `chassis.server.security`: the set the routes are built from."""
UNGUARDED_HERE = OPEN_PATHS | {"/metrics"}
"""Open routes, and `/metrics` (open outside the `cloud` profile; `test_cloud_profile_defaults`)."""
BOTH_SCOPES = "agent:run agent:read"


class RecordingOidcAuth(OidcAuth):
    """`OidcAuth` that records each token it was asked to check (None for no bearer)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[str | None] = []

    async def authenticate(self, token: str | None) -> Principal:
        self.calls.append(token)
        return await super().authenticate(token)


@pytest.fixture
def idp() -> Idp:
    idp = Idp()
    idp.add_rsa("k1")
    return idp


def _oidc(idp: Idp) -> RecordingOidcAuth:
    return RecordingOidcAuth(ISSUER, AUDIENCE, JWKS_URL, transport=httpx.MockTransport(idp.handler))


def _app(auth: OidcAuth) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
        auth=auth,
    )
    return create_app(ChassisConfig.model_validate(CONFIG), ports)


@asynccontextmanager
async def _client(app: FastAPI, auth: OidcAuth) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://chassis"
        ) as client,
    ):
        yield client
    await auth.aclose()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _wrong_tokens(idp: Idp) -> dict[str, str]:
    """OIDC bearers the IdP did not issue for this agent, one per way a JWT can be wrong."""
    now = int(time.time())
    good = idp.token(scope=BOTH_SCOPES)
    claims = jwt.decode(good, options={"verify_signature": False})
    forged = jwt.encode(claims, new_rsa(), algorithm="RS256", headers={"kid": "k1"})
    idp.add_rsa("k-unpublished", publish=False)
    return {
        "forged": forged,
        "unknown-key": idp.token("k-unpublished", scope=BOTH_SCOPES),
        "expired": idp.token(exp=now - 3600, iat=now - 7200, scope=BOTH_SCOPES),
        "other-issuer": idp.token(iss="https://other-idp.invalid", scope=BOTH_SCOPES),
        "other-audience": idp.token(aud="another-agent", scope=BOTH_SCOPES),
        "tampered": good[:-8] + "AAAAAAAA",
        "not-a-jwt": "tok-forged",
    }


def test_the_route_list_is_every_mounted_route_but_the_open_ones(idp: Idp) -> None:
    """The guard: every mounted path but `/health`, `/ready`, and `/metrics` is a run or read
    path, so a new route is either added to `RUN_PATHS` or `READ_PATHS` (and so to the tests
    below) or fails here."""
    mounted = {path for route in _app(_oidc(idp)).routes if (path := getattr(route, "path", None))}
    assert mounted - UNGUARDED_HERE == GUARDED
    assert {path for _, path, _ in RUN_ROUTES + READ_ROUTES} == GUARDED


@pytest.mark.parametrize(("method", "path", "body"), RUN_ROUTES + READ_ROUTES)
async def test_every_run_and_read_route_is_401_without_a_valid_oidc_bearer(
    idp: Idp, method: str, path: str, body: Any
) -> None:
    """No bearer, and every wrong OIDC bearer, is 401 Bearer; the token is never echoed."""
    auth = _oidc(idp)
    app = _app(auth)
    wrong = _wrong_tokens(idp)
    async with _client(app, auth) as client:
        missing = await client.request(method, path, json=body)
        refused = {
            name: await client.request(method, path, json=body, headers=_bearer(token))
            for name, token in wrong.items()
        }
    answers = {"missing": missing, **refused}
    seen = {
        name: (r.status_code, r.headers.get("www-authenticate", "").startswith("Bearer"))
        for name, r in answers.items()
    }
    assert seen == dict.fromkeys(answers, (401, True)), {n: r.text for n, r in answers.items()}
    leaked = [name for name, token in wrong.items() if token in refused[name].text]
    assert leaked == []
    assert auth.calls == [None, *wrong.values()]
    telemetry = app.state.ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    count = telemetry.counter_value(
        AUTH_REFUSED, code="unauthenticated", path_class=path_class(path)
    )
    assert count == len(answers)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [r for r in RUN_ROUTES if r[1] != MCP_PATH] + READ_ROUTES,
)
async def test_a_valid_oidc_bearer_with_the_scope_reaches_the_same_routes(
    idp: Idp, method: str, path: str, body: Any
) -> None:
    """The control: a JWT the IdP signed, with both scopes, gets 200 on the same routes."""
    auth = _oidc(idp)
    async with _client(_app(auth), auth) as client:
        response = await client.request(
            method, path, json=body, headers=_bearer(idp.token(scope=BOTH_SCOPES))
        )
    assert response.status_code == 200, response.text


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


async def test_the_mcp_hop_checks_the_callers_oidc_token_again(idp: Idp) -> None:
    """A valid MCP tool call runs, and its inner `/v1/run` hop is checked with the same JWT."""
    auth = _oidc(idp)
    app = _app(auth)
    token = idp.token(scope=BOTH_SCOPES)
    async with app.router.lifespan_context(app), _mcp_client(app, token) as client:
        result = await client.call_tool("echo", {"input": {"text": "hello"}})
    await auth.aclose()
    assert result.is_error is False, result
    assert auth.calls.count(token) >= 2
    assert None not in auth.calls, "the inner /v1/run call came without the caller's token"


async def test_an_unreadable_jwks_is_503_auth_unavailable(idp: Idp) -> None:
    """An IdP whose JWKS answers 503 is 503 `auth_unavailable` with `Retry-After`, not 401."""
    auth = _oidc(idp)
    token = idp.token(scope=BOTH_SCOPES)
    idp.status = 503
    async with _client(_app(auth), auth) as client:
        response = await client.post("/v1/run", json=RUN_BODY, headers=_bearer(token))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["detail"]["code"] == "auth_unavailable"
