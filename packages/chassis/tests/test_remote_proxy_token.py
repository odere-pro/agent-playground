"""R2-09: the remote listener's own token, apart from the token the chassis sends to the remote.

`spec.engine.auth.proxy_token_env` (and `proxy_previous_token_env` for a rotation) names the
bearer the remote listener accepts. Unset, the listener falls back to `token_env` and
`previous_token_env`, so today's configs keep working. With both set, a plain-A2A agent that
receives the outbound token cannot replay it against the model proxy or `/mcp`.

Driven with `httpx.ASGITransport`, no socket.
"""

from __future__ import annotations

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
from chassis.server.config import EngineAuthSpec
from chassis.server.remote_auth import RUN_SECRET, create_remote_proxy_app, remote_tokens
from fastapi import FastAPI
from pydantic import ValidationError

OUTBOUND = "o" * 64  # test values, not secrets
INBOUND = "i" * 64
INBOUND_OLD = "j" * 64
OUTBOUND_OLD = "q" * 64
SECRET = "s" * 43
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"  # pragma: allowlist secret (W3C example trace id)
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"
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
SPLIT = {
    "scheme": "bearer",
    "token_env": "REMOTE_TOKEN",
    "previous_token_env": "REMOTE_TOKEN_OLD",
    "proxy_token_env": "REMOTE_PROXY_TOKEN",
    "proxy_previous_token_env": "REMOTE_PROXY_TOKEN_OLD",
}


@pytest.fixture
def split_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", OUTBOUND)
    monkeypatch.setenv("REMOTE_TOKEN_OLD", OUTBOUND_OLD)
    monkeypatch.setenv("REMOTE_PROXY_TOKEN", INBOUND)
    monkeypatch.setenv("REMOTE_PROXY_TOKEN_OLD", INBOUND_OLD)


def _app(tokens: tuple[str, ...]) -> tuple[tuple[FastAPI, FastAPI], PortBundle]:
    ports = PortBundle(
        model=ScriptedModel([ScriptRule(match="simplify", reply="Plain words.")]),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )
    public = create_app(ChassisConfig.model_validate(CONFIG), ports)
    return (public, create_remote_proxy_app(public, tokens)), ports


def _run() -> tuple[Request, Context]:
    request = Request(
        request_id="req-split",
        trace_id=TRACE,
        idempotency_key="idem-req-split",
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
        run_secret=SECRET,
    )
    return request, ctx


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "traceparent": TRACEPARENT, RUN_SECRET: SECRET}


@pytest.mark.usefixtures("split_env")
async def test_the_outbound_token_is_401_on_the_listener_when_the_tokens_are_split() -> None:
    """R2-09 spec: given distinct outbound and inbound tokens, the outbound token with a valid
    traceparent and run secret is 401 `remote_unauthenticated`; the inbound token is let in."""
    (public, remote), ports = _app(remote_tokens(SPLIT))
    request, ctx = _run()
    transport = httpx.ASGITransport(app=remote)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        public.router.lifespan_context(public),
        public.state.runs.register(request, ctx),
    ):
        replayed = await client.post("/v1/chat/completions", json=BODY, headers=_headers(OUTBOUND))
        mcp = await client.post("/mcp", json={}, headers=_headers(OUTBOUND))
        inbound = await client.post("/v1/chat/completions", json=BODY, headers=_headers(INBOUND))
    assert replayed.status_code == 401, replayed.text
    assert replayed.json()["error"]["code"] == "remote_unauthenticated"
    assert mcp.status_code == 401
    assert mcp.json()["error"]["code"] == "remote_unauthenticated"
    assert inbound.status_code == 200, inbound.text
    model = ports.model
    assert isinstance(model, ScriptedModel) and len(model.calls) == 1


@pytest.mark.usefixtures("split_env")
def test_remote_tokens_reads_the_proxy_variables_when_set() -> None:
    """The listener's tokens are the proxy pair; neither outbound token is among them."""
    assert remote_tokens(SPLIT) == (INBOUND, INBOUND_OLD)
    assert remote_tokens({**SPLIT, "proxy_previous_token_env": None}) == (INBOUND,)


@pytest.mark.usefixtures("split_env")
def test_remote_tokens_falls_back_to_token_env_when_the_proxy_variable_is_unset() -> None:
    """With only `token_env` (and `previous_token_env`), the behavior is unchanged."""
    auth = {k: v for k, v in SPLIT.items() if not k.startswith("proxy_")}
    assert remote_tokens(auth) == (OUTBOUND, OUTBOUND_OLD)
    assert remote_tokens({**auth, "proxy_token_env": None}) == (OUTBOUND, OUTBOUND_OLD)


def test_an_unset_proxy_variable_is_refused_naming_only_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named proxy variable that is unset is an error, never a silent fall back to the
    outbound token."""
    monkeypatch.setenv("REMOTE_TOKEN", OUTBOUND)
    monkeypatch.delenv("REMOTE_PROXY_TOKEN", raising=False)
    auth = {
        "scheme": "bearer",
        "token_env": "REMOTE_TOKEN",
        "proxy_token_env": "REMOTE_PROXY_TOKEN",
    }
    with pytest.raises(LookupError, match="REMOTE_PROXY_TOKEN") as exc:
        remote_tokens(auth)
    assert OUTBOUND not in str(exc.value)


@pytest.mark.parametrize("slot", ["proxy_token_env", "proxy_previous_token_env"])
def test_a_short_proxy_token_is_refused_naming_only_the_variable(
    slot: str, split_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2-31 applies to the new variables: shorter than 32 characters is refused."""
    short = "x" * 31  # a test value, not a secret
    monkeypatch.setenv(SPLIT[slot], short)
    with pytest.raises(ValueError, match="shorter than 32 characters") as exc:
        remote_tokens(SPLIT)
    assert SPLIT[slot] in str(exc.value)
    assert short not in str(exc.value)


def test_the_config_takes_the_proxy_variables_by_name() -> None:
    """`spec.engine.auth` accepts both new names, optional, with the env-name pattern."""
    auth = EngineAuthSpec.model_validate(SPLIT)
    assert (auth.proxy_token_env, auth.proxy_previous_token_env) == (
        "REMOTE_PROXY_TOKEN",
        "REMOTE_PROXY_TOKEN_OLD",
    )
    bare = EngineAuthSpec.model_validate({"token_env": "REMOTE_TOKEN"})
    assert (bare.proxy_token_env, bare.proxy_previous_token_env) == (None, None)
    with pytest.raises(ValidationError):
        EngineAuthSpec.model_validate({**SPLIT, "proxy_token_env": "not-a-name"})


def test_a_proxy_previous_token_without_a_proxy_token_is_refused() -> None:
    """A rotation of the listener's token needs the listener's own token first."""
    with pytest.raises(ValidationError, match="proxy_previous_token_env needs"):
        EngineAuthSpec.model_validate(
            {"token_env": "REMOTE_TOKEN", "proxy_previous_token_env": "REMOTE_PROXY_TOKEN_OLD"}
        )
