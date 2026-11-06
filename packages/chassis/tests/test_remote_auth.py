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
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.remote_auth import (
    AUTH_FAILED,
    RUN_REQUIRED,
    RUN_SECRET,
    RUN_SECRET_MAX,
    RUN_SECRET_SKIPPED,
    BearerAuth,
    RequireRun,
    create_remote_proxy_app,
    remote_tokens,
)
from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

CURRENT = "c" * 64  # test values, not secrets
PREVIOUS = "p" * 64
SECRET = "s" * 43  # the run secret of the default run (R2-02)
SECRET_B = "b" * 43
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
        state=InMemoryState(),
    )
    public = create_app(ChassisConfig.model_validate(CONFIG), ports)
    remote = create_remote_proxy_app(public, (CURRENT, PREVIOUS))
    return public, remote, ports


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chassis")


def _run(
    request_id: str = "req-remote", trace: str = TRACE, secret: str | None = None
) -> tuple[Request, Context]:
    request = Request(
        request_id=request_id,
        trace_id=trace,
        idempotency_key=f"idem-{request_id}",
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
        run_secret=SECRET if secret is None else secret,
    )
    return request, ctx


def _auth(token: str, secret: str = SECRET) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "traceparent": TRACEPARENT, RUN_SECRET: secret}


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
    refusals = [
        (r.status_code, r.json(), r.headers["www-authenticate"])
        for r in (missing, wrong, not_bearer)
    ]
    assert refusals == [(401, UNAUTHENTICATED, "Bearer")] * 3
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


@pytest.mark.parametrize(
    ("path", "body", "traceparent"),
    [
        ("/v1/chat/completions", BODY, None),
        ("/v1/chat/completions", BODY, OTHER),
        ("/v1/chat/completions", BODY, "not-a-traceparent"),
        ("/mcp", {}, OTHER),
    ],
    ids=["no-header", "unknown", "bad", "tools"],
)
async def test_the_right_token_with_no_run_is_403_run_required(
    path: str, body: dict[str, Any], traceparent: str | None
) -> None:
    """H17: a remote holding the token cannot spend outside a run the chassis opened."""
    public, remote, ports = _app()
    request, ctx = _run()
    headers = {"Authorization": f"Bearer {CURRENT}"}
    if traceparent is not None:
        headers["traceparent"] = traceparent
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        response = await client.post(path, json=body, headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "run_required"
    model = ports.model
    assert isinstance(model, ScriptedModel) and model.calls == []


async def test_after_the_run_ends_its_traceparent_is_403() -> None:
    """A run's traceparent is let in during the run and 403 after it ends."""
    public, remote, _ = _app()
    request, ctx = _run()
    async with _client(remote) as client, public.router.lifespan_context(public):
        async with public.state.runs.register(request, ctx):
            inside = await client.post("/v1/chat/completions", json=BODY, headers=_auth(CURRENT))
        after = await client.post("/v1/chat/completions", json=BODY, headers=_auth(CURRENT))
    assert (inside.status_code, after.status_code) == (200, 403)


@pytest.mark.parametrize(
    "path", ["/dapr/subscribe", "/dapr/events/x", "/docs", "/openapi.json", "/v1/run"]
)
async def test_dapr_docs_and_other_paths_are_404_even_with_the_token(path: str) -> None:
    """The remote app is an explicit list of routes: only the model proxy and `/mcp`."""
    public, remote, _ = _app()
    request, ctx = _run()
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        response = await client.get(path, headers=_auth(CURRENT))
        unauthenticated = await client.get("/dapr/subscribe")
    assert response.status_code == 404
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
    """The remote listener serves exactly chat, Anthropic proxy, and `/mcp` (v5 A.6)."""
    _, remote, _ = _app()
    assert _route_paths(remote.routes) == {"/v1/chat/completions", "/v1/messages", "/mcp"}


ANTHROPIC_PATH = "/v1/messages"


async def test_the_anthropic_proxy_route_refusals_are_in_the_anthropic_body() -> None:
    """401 and 403 on the Anthropic proxy route use Anthropic's error body (v5 A.8)."""
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
    refusals = [
        (
            r.status_code,
            r.json()["type"],
            r.json()["error"],
            r.json()["request_id"] == r.headers["request-id"],
            r.headers["www-authenticate"],
            r.headers["x-should-retry"],
        )
        for r in (none, key_only, both)
    ]
    refused = {
        "type": "authentication_error",
        "message": "remote_unauthenticated: missing or invalid bearer token",
    }
    assert refusals == [(401, "error", refused, True, "Bearer", "false")] * 3
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


@pytest.mark.parametrize("path", ["/v1/messages/count_tokens", "/v1/models"])
async def test_count_tokens_and_models_are_404_with_a_token_and_401_without(path: str) -> None:
    """Auth comes first: 404 with the token, and v4's 401 body without it (contract v5, A.8)."""
    public, remote, _ = _app()
    request, ctx = _run()
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        allowed = await client.post(path, json={}, headers=_auth(CURRENT))
        denied = await client.post(path, json={})
        counted = await client.post("/v1/messages/count_tokens", json={}, headers=_auth(CURRENT))
    assert allowed.status_code == 404
    assert denied.status_code == 401 and denied.json() == UNAUTHENTICATED
    assert counted.json()["error"]["type"] == "not_found_error"


async def test_a_websocket_to_the_anthropic_proxy_route_is_closed_with_1008() -> None:
    """A websocket to the Anthropic proxy route is closed with 1008."""
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
    """`BearerAuth` strips `Authorization` before the route sees the request."""
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
    """`BearerAuth` refuses an empty token list and an empty token."""
    with pytest.raises(ValueError, match="at least one token"):
        BearerAuth(lambda *_: None, (), state=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one token"):
        BearerAuth(lambda *_: None, ("",), state=None)  # type: ignore[arg-type]


def test_remote_tokens_reads_the_named_variables_and_never_shows_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`remote_tokens` reads the named variables and never shows a value."""
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


@pytest.mark.parametrize("middleware_index", [0, 1], ids=["bearer-auth", "require-run"])
@pytest.mark.parametrize(
    ("kind", "headers", "want"),
    [
        (
            "websocket",
            [(b"authorization", f"Bearer {CURRENT}".encode())],
            [{"type": "websocket.close", "code": 1008}],
        ),
        ("webtransport", [], []),
        ("something-else", [], []),
    ],
    ids=["websocket", "webtransport", "something-else"],
)
async def test_non_http_scopes_are_refused_and_never_reach_the_app(
    kind: str, headers: list[tuple[bytes, bytes]], want: list[Any], middleware_index: int
) -> None:
    """F4: every scope but http and lifespan is refused; a websocket is closed with 1008."""
    inner = _Recorder()
    middleware = _wrapped(inner)[middleware_index]
    scope: Scope = {"type": kind, "headers": list(headers), "path": "/mcp"}
    sent = await _drive(middleware, scope)
    assert inner.scopes == [], "the inner app was never called"
    assert sent == want


async def test_lifespan_scope_passes_through() -> None:
    """Both middlewares pass the lifespan scope to the app."""
    for middleware in _wrapped(inner := _Recorder()):
        await _drive(middleware, {"type": "lifespan"})
    assert inner.scopes == ["lifespan", "lifespan"]


RUN_B_TRACE = "0af7651916cd43dd8448eb211c80319c"  # pragma: allowlist secret


async def test_require_run_refuses_another_runs_traceparent_when_two_runs_share_one_remote() -> (
    None
):
    """A remote serving run A cannot spend under run B with the shared bearer and B's trace."""
    public, remote, _ = _app()
    run_a, ctx_a = _run("req-a", TRACE)
    run_b, ctx_b = _run("req-b", RUN_B_TRACE, SECRET_B)
    headers = {"Authorization": f"Bearer {CURRENT}", "traceparent": OTHER}

    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(run_a, ctx_a),
        public.state.runs.register(run_b, ctx_b),
    ):
        answer = await client.post("/v1/chat/completions", json=BODY, headers=headers)

    assert answer.status_code == 403


# --- R2-02: the per-run secret (contract v8, item 4) ---

PLAIN_CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {
            "connector": "remote",
            "url": "http://remote.test:8443",
            "auth": {"scheme": "bearer", "token_env": "POC05_TEST_REMOTE_TOKEN"},
            "protocol": "a2a",
        }
    },
}


def _counted(ports: PortBundle, reason: str) -> int:
    telemetry = ports.telemetry
    assert isinstance(telemetry, InMemoryTelemetry)
    return telemetry.counter_value(RUN_REQUIRED, reason=reason)


async def test_require_run_refuses_run_a_secret_with_run_b_traceparent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R2-02: run A's secret with run B's traceparent is 403, and B's record is untouched."""
    public, remote, ports = _app()
    run_a, ctx_a = _run("req-a", TRACE, SECRET)
    run_b, ctx_b = _run("req-b", RUN_B_TRACE, SECRET_B)
    headers = {"Authorization": f"Bearer {CURRENT}", "traceparent": OTHER, RUN_SECRET: SECRET}
    caplog.set_level(logging.DEBUG)
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(run_a, ctx_a),
        public.state.runs.register(run_b, ctx_b) as record_b,
    ):
        key_before = record_b.keyed_run_key
        answer = await client.post("/v1/chat/completions", json=BODY, headers=headers)
        own = await client.post(
            "/v1/chat/completions", json=BODY, headers={**headers, RUN_SECRET: SECRET_B}
        )
        after = (record_b.spent_tokens, record_b.reserved_tokens, record_b.keyed_run_key)
    assert answer.status_code == 403 and answer.json()["error"]["code"] == "run_required"
    assert own.status_code == 200, own.text
    assert after[1] == 0 and after[2] == key_before
    assert record_b.model_calls == 1, "only the call with B's own secret was charged to B"
    assert _counted(ports, "secret_wrong") == 1
    assert SECRET not in caplog.text and SECRET_B not in caplog.text


async def test_require_run_refuses_a_missing_secret_with_403() -> None:
    """A run in flight, the right bearer and traceparent, no secret: 403 `secret_missing`."""
    public, remote, ports = _app()
    request, ctx = _run()
    headers = {"Authorization": f"Bearer {CURRENT}", "traceparent": TRACEPARENT}
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx) as record,
    ):
        answer = await client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert answer.status_code == 403 and answer.json()["error"]["code"] == "run_required"
    assert record.model_calls == 0
    assert _counted(ports, "secret_missing") == 1


@pytest.mark.parametrize(
    "secrets",
    [[SECRET, SECRET], [SECRET + "x"], ["x" * (RUN_SECRET_MAX + 1)], [SECRET[:-1]]],
    ids=["two-headers", "longer", "over-the-cap", "shorter"],
)
async def test_require_run_refuses_two_secret_headers_and_a_wrong_one(secrets: list[str]) -> None:
    """More than one secret header, or one that is not the run's, is 403 `secret_wrong`."""
    public, remote, ports = _app()
    request, ctx = _run()
    headers = [
        ("authorization", f"Bearer {CURRENT}"),
        ("traceparent", TRACEPARENT),
        *[(RUN_SECRET, value) for value in secrets],
    ]
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        answer = await client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert answer.status_code == 403
    assert _counted(ports, "secret_wrong") == 1


class _Headers:
    """An inner ASGI app that records each request's headers and answers 204."""

    def __init__(self) -> None:
        self.seen: list[list[tuple[bytes, bytes]]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.seen.append(list(scope["headers"]))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _state(config: dict[str, Any]) -> Any:
    """What `RequireRun` reads from the app state: the config, the runs, the ports."""
    public, _, ports = _app()
    state = type("S", (), {})()
    state.config = ChassisConfig.model_validate(config)
    state.runs = public.state.runs
    state.ports = ports
    return state


async def test_require_run_lets_the_right_secret_in_and_the_header_never_reaches_the_route() -> (
    None
):
    """The right secret is let in, and the header is stripped before the inner app."""
    inner = _Headers()
    state = _state(CONFIG)
    request, ctx = _run()
    headers = {"traceparent": TRACEPARENT, RUN_SECRET: SECRET}
    async with (
        _client(RequireRun(inner, state=state)) as client,
        state.runs.register(request, ctx),
    ):
        answer = await client.get("/x", headers=headers)
    assert answer.status_code == 204
    assert [n for n, _ in inner.seen[0] if n.lower() == RUN_SECRET.encode()] == []
    assert [n for n, _ in inner.seen[0] if n.lower() == b"traceparent"] == [b"traceparent"]


async def test_require_run_lets_a_plain_mode_run_in_without_a_secret() -> None:
    """`spec.engine.protocol: a2a` (kagent-adk) never sees `ctx`: traceparent only, counted."""
    inner = _Headers()
    state = _state(PLAIN_CONFIG)
    request, ctx = _run()
    plain_ctx = ctx.model_copy(update={"run_secret": None})
    async with _client(RequireRun(inner, state=state)) as client:
        async with state.runs.register(request, plain_ctx):
            answer = await client.get("/x", headers={"traceparent": TRACEPARENT})
            stray = await client.get("/x", headers={"traceparent": TRACEPARENT, RUN_SECRET: "x"})
        ended = await client.get("/x", headers={"traceparent": TRACEPARENT})
    assert (answer.status_code, stray.status_code, ended.status_code) == (204, 204, 403)
    assert all(n.lower() != RUN_SECRET.encode() for headers in inner.seen for n, _ in headers)
    telemetry = state.ports.telemetry
    assert telemetry.counter_value(RUN_SECRET_SKIPPED, reason="lane_without_secret") == 2


async def test_require_run_refuses_a_chassis_mode_run_that_holds_no_secret() -> None:
    """Fail closed: a chassis-mode record without a secret lets no call in."""
    inner = _Headers()
    state = _state(CONFIG)
    request, ctx = _run()
    async with (
        _client(RequireRun(inner, state=state)) as client,
        state.runs.register(request, ctx.model_copy(update={"run_secret": None})),
    ):
        answer = await client.get("/x", headers={"traceparent": TRACEPARENT, RUN_SECRET: ""})
    assert answer.status_code == 403 and inner.seen == []


@pytest.mark.parametrize("path", ["/v1/chat/completions", ANTHROPIC_PATH])
async def test_require_run_body_bytes_are_the_same_for_no_run_and_wrong_secret(path: str) -> None:
    """No run, a missing secret, and a wrong secret answer the same bytes: no oracle."""
    public, remote, _ = _app()
    request, ctx = _run()
    bearer = {"Authorization": f"Bearer {CURRENT}"}
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        answers = [
            await client.post(path, json=BODY, headers={**bearer, "traceparent": OTHER}),
            await client.post(path, json=BODY, headers={**bearer, "traceparent": TRACEPARENT}),
            await client.post(path, json=BODY, headers=_auth(CURRENT, SECRET_B)),
        ]
    assert {a.status_code for a in answers} == {403}
    if path == ANTHROPIC_PATH:  # each body carries its own fresh request id
        shapes = {(a.json()["type"], str(a.json()["error"])) for a in answers}
        assert len(shapes) == 1
        assert {a.headers.get("x-should-retry") for a in answers} == {"false"}
    else:
        assert answers[0].content == answers[1].content == answers[2].content


async def test_mcp_on_the_remote_listener_refuses_a_wrong_secret() -> None:
    """The tool endpoint sits behind `RequireRun` too: a wrong secret is 403, the right one 200."""
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
    accept = {"accept": "application/json, text/event-stream"}
    async with (
        _client(remote) as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        wrong = await client.post("/mcp", json=body, headers={**_auth(CURRENT, SECRET_B), **accept})
        right = await client.post("/mcp", json=body, headers={**_auth(CURRENT), **accept})
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "run_required"
    assert right.status_code == 200, right.text
