"""CH-14 (issue 134): the security defaults of the `cloud` profile.

Each is a value the `cloud` profile fills when the document leaves it unset, and refuses at load
(and on a reload) when the document names a weaker one; `fake` and `local` keep the old default
and accept any value.

- `spec.limits.uncorrelated_tokens_per_minute: 0`. A cap of 0 refuses a call on the loopback
  model proxy that names no run in flight with 403 `uncorrelated_refused` (suggested), before the
  model is called, on both proxy routes. A correlated call is never touched.
- `spec.observability.metrics_scope: agent:read` (P-4 option 1; `spec.security.read_scope`):
  `/metrics` needs a token with that scope.
- `spec.limits.rate_limit_on_unavailable: fail_closed` (P-26 option 1): a rate-limit store
  outage refuses a counted call with 503 `state_unavailable`.

Driven in process, no socket.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx
import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.auth import StaticTokens
from chassis.fakes.state import InMemoryState
from chassis.ports.auth import Principal
from chassis.ports.state import StateUnavailable
from chassis.profiles import Profile
from chassis.server import ChassisConfig, create_app
from chassis.server.config import load_config
from chassis.server.config_loader import ConfigRejected, ConfigReloader, parse_document
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    PLAIN_WORDS,
    TRACEPARENT,
    asgi_client,
    proxy_ports,
    run_request,
    telemetry_of,
)
from starlette.datastructures import State

CHAT = "/v1/chat/completions"
MESSAGES = "/v1/messages"
DEFAULT_CAP = 20_000


def _document(
    profile: Profile, limits: dict[str, Any] | None = None, **sections: Any
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "engine": (
            {"connector": "sidecar"}
            if profile == "cloud"
            else {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}
        ),
        "model": {"route": "big-default"},
    }
    if profile == "cloud":
        spec["trust"] = "trusted"
    if limits is not None:
        spec["limits"] = limits
    spec.update(sections)
    return {"profile": profile, "agent": {"name": "echo", "version": "0.0.1"}, "spec": spec}


class _SidecarEngine(FakeEngine):
    """The echo engine, labelled `sidecar`: `cloud` refuses `inprocess`. The proxy routes under
    test never call the engine."""

    kind = "sidecar"


def _app(document: dict[str, Any], **ports_extra: Any) -> tuple[Any, Any, ScriptedModel]:
    """The app and its proxy. Outside `fake` the lifespan loads the document from `ConfigPort`,
    so the store holds it too: the `cloud` default is checked on that path as well."""
    model = ScriptedModel(PLAIN_WORDS)
    engine = _SidecarEngine(handle=echo) if document["profile"] == "cloud" else None
    ports = replace(
        proxy_ports(model, engine), config=InMemoryConfig({"echo": document}), **ports_extra
    )
    app = create_app(load_config(document), ports)
    return app, create_proxy_app(app), model


# The uncorrelated cap: the profile default.


def test_cloud_defaults_the_uncorrelated_cap_to_zero() -> None:
    assert load_config(_document("cloud")).spec.limits.uncorrelated_tokens_per_minute == 0


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_fake_and_local_keep_the_default_cap(profile: Profile) -> None:
    loaded = load_config(_document(profile))
    assert loaded.spec.limits.uncorrelated_tokens_per_minute == DEFAULT_CAP


WEAKENING = [
    ({"limits": {"uncorrelated_tokens_per_minute": 500}}, "uncorrelated_tokens_per_minute"),
    ({"limits": {"rate_limit_on_unavailable": "fail_open"}}, "rate_limit_on_unavailable"),
    ({"observability": {"metrics_scope": None}}, "metrics_scope"),
]
"""A document that names a value weaker than the `cloud` default, and the field it names."""


@pytest.mark.parametrize(("sections", "field"), WEAKENING)
def test_cloud_refuses_a_document_that_weakens_a_default(
    sections: dict[str, Any], field: str
) -> None:
    """Refused at load, not only defaulted: the message names the field, never only the value."""
    with pytest.raises(ValueError, match=field) as info:
        load_config(_document("cloud", **sections))
    assert "profile 'cloud'" in str(info.value)


@pytest.mark.parametrize(("sections", "field"), WEAKENING)
@pytest.mark.parametrize("profile", ["fake", "local"])
def test_fake_and_local_accept_the_same_values(
    profile: Profile, sections: dict[str, Any], field: str
) -> None:
    load_config(_document(profile, **sections))


@pytest.mark.parametrize(
    "sections",
    [
        {"limits": {"uncorrelated_tokens_per_minute": 0}},
        {"limits": {"rate_limit_on_unavailable": "fail_closed"}},
        {"observability": {"metrics_scope": "metrics:read"}},
    ],
    ids=["zero-cap", "fail-closed", "another-scope"],
)
def test_cloud_accepts_a_document_that_names_a_value_as_strong(sections: dict[str, Any]) -> None:
    load_config(_document("cloud", **sections))


def test_a_cloud_config_survives_a_dump_and_reload() -> None:
    """The filled defaults are as strong as the rule, so a round trip is not refused."""
    loaded = load_config(_document("cloud"))
    again = ChassisConfig.model_validate(loaded.model_dump(mode="json", exclude_unset=True))
    assert again.spec.limits == loaded.spec.limits
    assert again.spec.observability == loaded.spec.observability


@pytest.mark.parametrize(("sections", "field"), WEAKENING)
async def test_a_reload_that_weakens_a_default_is_refused_and_the_old_config_stays(
    sections: dict[str, Any], field: str
) -> None:
    store = InMemoryConfig({"echo": _document("cloud")})
    telemetry = InMemoryTelemetry()
    state = State({"config": None, "config_loaded": False})
    loader = ConfigReloader(
        state, store, load_config(_document("cloud")), telemetry=telemetry, from_store=True
    )
    await loader.start()
    good = state.config
    with pytest.raises(ConfigRejected):
        parse_document(_document("cloud", **sections))
    await store.put("echo", _document("cloud", **sections))
    assert state.config is good
    assert telemetry.counter_value("chassis.config.rejected", reason="invalid") == 1
    await loader.stop()


def test_cloud_keeps_the_other_limits_when_the_document_sets_some() -> None:
    loaded = load_config(_document("cloud", {"messages_max": 8}))
    assert loaded.spec.limits.messages_max == 8
    assert loaded.spec.limits.uncorrelated_tokens_per_minute == 0


def test_the_default_does_not_change_the_document_it_was_given() -> None:
    document = _document("cloud", {"messages_max": 8})
    load_config(document)
    assert document["spec"]["limits"] == {"messages_max": 8}


# The uncorrelated cap: the proxy routes.


async def test_cloud_refuses_an_uncorrelated_chat_call_before_the_model() -> None:
    app, proxy, model = _app(_document("cloud"))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(CHAT, json=ANTHROPIC_BODY)
    assert res.status_code == 403, res.text
    error = res.json()["error"]
    assert error["code"] == "uncorrelated_refused"
    assert error["type"] == "uncorrelated_refused"
    assert error["retryable"] is False
    assert model.calls == []
    refused = telemetry_of(app.state.ports).counter_value(
        "chassis.model_calls_refused", route="big-default"
    )
    assert refused == 1


async def test_cloud_refuses_an_uncorrelated_streamed_chat_call() -> None:
    app, proxy, model = _app(_document("cloud"))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(CHAT, json={**ANTHROPIC_BODY, "stream": True})
    assert res.status_code == 403
    assert "uncorrelated_refused" in res.text
    assert model.calls == []


async def test_cloud_refuses_an_uncorrelated_messages_call_before_the_model() -> None:
    app, proxy, model = _app(_document("cloud"))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(MESSAGES, json=ANTHROPIC_BODY)
    assert res.status_code == 403, res.text
    error = res.json()["error"]
    assert error["type"] == "permission_error"
    assert error["message"].startswith("uncorrelated_refused: ")
    assert res.headers["x-should-retry"] == "false"
    assert model.calls == []


async def test_cloud_refuses_a_traceparent_that_names_no_run_in_flight() -> None:
    app, proxy, _ = _app(_document("cloud"))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(CHAT, json=ANTHROPIC_BODY, headers={"traceparent": TRACEPARENT})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "uncorrelated_refused"


async def test_cloud_still_serves_a_call_that_names_its_run() -> None:
    """The paired control: the same call, correlated, is served under `cloud`."""
    app, proxy, model = _app(_document("cloud"))
    request, ctx = run_request(max_tokens=100)
    async with (
        asgi_client(proxy) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx),
    ):
        res = await client.post(CHAT, json=ANTHROPIC_BODY, headers={"traceparent": TRACEPARENT})
    assert res.status_code == 200, res.text
    assert len(model.calls) == 1


@pytest.mark.parametrize("path", [CHAT, MESSAGES])
@pytest.mark.parametrize("profile", ["fake", "local"])
async def test_fake_and_local_still_serve_an_uncorrelated_call(profile: Profile, path: str) -> None:
    app, proxy, model = _app(_document(profile))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(path, json=ANTHROPIC_BODY)
    assert res.status_code == 200, res.text
    assert len(model.calls) == 1


async def test_a_zero_cap_in_any_profile_is_uncorrelated_refused() -> None:
    """The code follows the cap, not the profile: `0` means refuse all, everywhere."""
    app, proxy, _ = _app(_document("fake", {"uncorrelated_tokens_per_minute": 0}))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(CHAT, json=ANTHROPIC_BODY)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "uncorrelated_refused"


async def test_a_spent_cap_above_zero_is_still_budget_exhausted() -> None:
    """A cap above 0 keeps its 429 `budget_exhausted`: it may fit again next minute."""
    limits = {"uncorrelated_tokens_per_minute": 10}
    app, proxy, _ = _app(_document("fake", limits))
    async with asgi_client(proxy) as client, app.router.lifespan_context(app):
        res = await client.post(CHAT, json={**ANTHROPIC_BODY, "max_tokens": 64})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "budget_exhausted"


# `/metrics` behind `agent:read` (P-4 option 1).

TOKENS = {
    "tok-run": Principal("user-run", frozenset({"agent:run"})),
    "tok-read": Principal("prometheus", frozenset({"agent:read"})),
    "tok-renamed": Principal("prometheus", frozenset({"ops:read"})),
}


def test_cloud_defaults_the_metrics_scope_to_the_read_scope() -> None:
    assert load_config(_document("cloud")).spec.observability.metrics_scope == "agent:read"


def test_cloud_follows_a_renamed_read_scope() -> None:
    document = _document("cloud", security={"read_scope": "ops:read"})
    assert load_config(document).spec.observability.metrics_scope == "ops:read"


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_fake_and_local_keep_metrics_open(profile: Profile) -> None:
    assert load_config(_document(profile)).spec.observability.metrics_scope is None


async def _metrics(document: dict[str, Any], token: str | None) -> httpx.Response:
    app, _, _ = _app(document, auth=StaticTokens(TOKENS))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with asgi_client(app) as client, app.router.lifespan_context(app):
        return await client.get("/metrics", headers=headers)


@pytest.mark.parametrize(
    ("token", "status"), [(None, 401), ("tok-forged", 401), ("tok-run", 403), ("tok-read", 200)]
)
async def test_cloud_metrics_need_a_token_with_agent_read(token: str | None, status: int) -> None:
    res = await _metrics(_document("cloud"), token)
    assert res.status_code == status, res.text
    if status == 200:
        assert "chassis_requests_total" in res.text


@pytest.mark.parametrize("profile", ["fake", "local"])
async def test_fake_and_local_metrics_need_no_token_with_auth_on(profile: Profile) -> None:
    res = await _metrics(_document(profile), None)
    assert res.status_code == 200, res.text


# A rate-limit store outage fails closed (P-26 option 1).

RUN_BODY = {"input": {"text": "hello"}}


def test_cloud_defaults_the_rate_limit_to_fail_closed() -> None:
    limits = load_config(_document("cloud")).spec.limits
    assert limits.rate_limit_on_unavailable == "fail_closed"


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_fake_and_local_keep_the_rate_limit_fail_open(profile: Profile) -> None:
    limits = load_config(_document(profile)).spec.limits
    assert limits.rate_limit_on_unavailable == "fail_open"


async def _run_in_an_outage(document: dict[str, Any]) -> tuple[httpx.Response, httpx.Response]:
    """One counted call while the store is down, then the same call with it back (the control)."""
    state = InMemoryState()
    app, _, _ = _app(document, auth=StaticTokens(TOKENS), state=state)
    headers = {"Authorization": "Bearer tok-run"}
    async with asgi_client(app) as client, app.router.lifespan_context(app):
        state.fail_next(StateUnavailable("down"))
        down = await client.post("/v1/run", json=RUN_BODY, headers=headers)
        back = await client.post("/v1/run", json=RUN_BODY, headers=headers)
    return down, back


async def test_cloud_refuses_a_counted_call_when_the_rate_limit_store_is_down() -> None:
    down, back = await _run_in_an_outage(_document("cloud"))
    assert down.status_code == 503, down.text
    assert "state_unavailable" in down.text
    assert back.status_code == 200, back.text


@pytest.mark.parametrize("profile", ["fake", "local"])
async def test_fake_and_local_let_a_counted_call_through_an_outage(profile: Profile) -> None:
    down, back = await _run_in_an_outage(_document(profile))
    assert down.status_code == 200, down.text
    assert back.status_code == 200, back.text
