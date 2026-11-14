"""012 H-3 hardening, from the platform-security review of the run's-route check (PR 198).

1. Metric labels. A model call is counted by the route it was admitted on, or by `other` when it is
   refused and names a route the config does not declare. The workload's own `model` string never
   becomes a label, so refused calls with new names add no metric series. `model` is capped at
   `MODEL_MAX_CHARS` on both model proxy routes.
2. An uncorrelated call while a run is in flight. In a lane without `RequireRun` a workload can
   drop `traceparent`. The call is then pinned to `spec.model.route` (R6) and charged to the
   uncorrelated cap, never to the run; under a cap of `0` it is refused. This pins the gap that
   `docs/specs/models.md` names.

Offline, in process.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from chassis.adapters.anthropic_compat.model_wire import MODEL_MAX_CHARS
from chassis.core.envelope import Context
from chassis.fakes import InMemoryTelemetry
from chassis.server import ChassisConfig, create_app
from chassis.server import model_proxy_messages as route_module
from chassis.server.model_admission import OTHER_ROUTE, PINNED, ROUTE_SPAN, UNRESOLVED
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    PLAIN_WORDS,
    TRACEPARENT,
    GatedModel,
    RecordingModel,
    asgi_client,
    proxy_ports,
    run_request,
    telemetry_of,
)

DEFAULT = "big-default"
RESOLVED = "small-default"
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {
            "route": DEFAULT,
            "fallback_route": "fallback-route",
            "routes": {"simplify": RESOLVED},
        },
    },
}
DECLARED = {DEFAULT, RESOLVED, "fallback-route"}
PATHS = ["/v1/chat/completions", "/v1/messages"]
CALLS = 12
BUDGET = 1000


def _names(count: int) -> list[str]:
    """`count` distinct route names, each as long as `model` may be."""
    return [f"{i:04d}-".ljust(MODEL_MAX_CHARS, "x") for i in range(count)]


def _route_labels(telemetry: InMemoryTelemetry) -> set[str]:
    """Every value the `route` label took, on every counter."""
    return {
        str(value) for (_, labels) in telemetry.counters for key, value in labels if key == "route"
    }


def _config(**limits: Any) -> ChassisConfig:
    document = json.loads(json.dumps(CONFIG))
    if limits:
        document["spec"]["limits"] = limits
    return ChassisConfig.model_validate(document)


def _in_run(ctx: Context) -> Context:
    return ctx.model_copy(update={"model_route": RESOLVED})


@pytest.mark.parametrize("path", PATHS)
async def test_refused_calls_with_new_long_names_add_no_metric_series(path: str) -> None:
    """N refused calls that name N distinct, long, undeclared routes: every counter's `route`
    label stays within the declared routes and `other`, and `chassis.model_calls{route=other}`
    counts them."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(_config(), ports)
    request, ctx = run_request(BUDGET)
    headers = {"traceparent": TRACEPARENT}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _in_run(ctx)),
    ):
        statuses = [
            (
                await client.post(path, json={**ANTHROPIC_BODY, "model": name}, headers=headers)
            ).status_code
            for name in _names(CALLS)
        ]
    telemetry = telemetry_of(ports)
    assert statuses == [403] * CALLS
    assert model.calls == []
    assert _route_labels(telemetry) <= DECLARED | {OTHER_ROUTE, UNRESOLVED}
    assert telemetry.counter_value("chassis.model_calls", route=OTHER_ROUTE) == CALLS


@pytest.mark.parametrize("path", PATHS)
async def test_a_refused_call_on_a_declared_route_keeps_that_label(path: str) -> None:
    """A declared route the run may not use is a bounded label: it is counted under its name."""
    ports = proxy_ports(RecordingModel(PLAIN_WORDS))
    app = create_app(_config(), ports)
    request, ctx = run_request(BUDGET)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _in_run(ctx)),
    ):
        res = await client.post(
            path, json={**ANTHROPIC_BODY, "model": DEFAULT}, headers={"traceparent": TRACEPARENT}
        )
    assert res.status_code == 403, res.text
    assert telemetry_of(ports).counter_value("chassis.model_calls", route=DEFAULT) == 1


@pytest.mark.parametrize("path", PATHS)
async def test_uncorrelated_calls_with_new_names_count_the_pinned_route(path: str) -> None:
    """Uncorrelated calls are counted by `spec.model.route`, the route they are sent to."""
    ports = proxy_ports(RecordingModel(PLAIN_WORDS))
    app = create_app(_config(), ports)
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        statuses = [
            (await client.post(path, json={**ANTHROPIC_BODY, "model": name})).status_code
            for name in _names(CALLS)
        ]
    telemetry = telemetry_of(ports)
    assert statuses == [200] * CALLS
    assert _route_labels(telemetry) == {DEFAULT}
    assert telemetry.counter_value("chassis.model_calls", route=DEFAULT) == CALLS


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("path", PATHS)
async def test_an_over_long_model_is_refused_before_admission(path: str, stream: bool) -> None:
    """A `model` over `MODEL_MAX_CHARS` is refused with the route's 4xx for a bad body: the model
    is never called and nothing is counted."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(_config(), ports)
    body = {**ANTHROPIC_BODY, "model": "m" * (MODEL_MAX_CHARS + 1), "stream": stream}
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post(path, json=body)
    assert res.status_code in (400, 422), res.text
    assert model.calls == []
    assert telemetry_of(ports).counters == {}


async def test_an_uncorrelated_stream_timeout_counts_the_pinned_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Anthropic route's stream deadline counts `chassis.model_stream_timeout` by the route the
    call was sent to, never by the `model` the workload named."""
    monkeypatch.setattr(route_module, "UNCORRELATED_STREAM_DEADLINE_S", 0.2)
    model = GatedModel()
    ports = proxy_ports(model)
    app = create_app(_config(), ports)
    body = {**ANTHROPIC_BODY, "model": _names(1)[0], "max_tokens": 30, "stream": True}
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await asyncio.wait_for(client.post("/v1/messages", json=body), 5)
    telemetry = telemetry_of(ports)
    assert res.status_code == 200, res.text
    assert telemetry.counter_value("chassis.model_stream_timeout", route=DEFAULT) == 1
    assert _route_labels(telemetry) == {DEFAULT}


@pytest.mark.parametrize("path", PATHS)
async def test_an_uncorrelated_call_during_a_run_is_pinned_and_capped(path: str) -> None:
    """Known gap (`docs/specs/models.md`): with no `RequireRun`, a call that drops `traceparent`
    while its run is in flight on a task route is served on `spec.model.route`. It is charged to
    the uncorrelated cap, never to the run."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(_config(), ports)
    request, ctx = run_request(BUDGET)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _in_run(ctx)) as record,
    ):
        res = await client.post(path, json={**ANTHROPIC_BODY, "model": RESOLVED})
        cap = app.state.uncorrelated_cap
    telemetry = telemetry_of(ports)
    assert res.status_code == 200, res.text
    assert len(model.calls) == 1
    (span,) = [s for s in telemetry.spans if s.name == ROUTE_SPAN]
    assert (span.attributes["route"], span.attributes["route.forced"]) == (DEFAULT, True)
    assert telemetry.counter_value(PINNED, route=DEFAULT) == 1
    assert telemetry.counter_value("chassis.model_calls_uncorrelated", route=DEFAULT) == 1
    assert cap.spent > 0
    assert (record.spent_tokens, record.reserved_tokens) == (0, 0)


@pytest.mark.parametrize("path", PATHS)
async def test_an_uncorrelated_call_during_a_run_is_refused_under_a_cap_of_0(path: str) -> None:
    """Under `uncorrelated_tokens_per_minute: 0` (the `cloud` default) that call is 403
    `uncorrelated_refused`, and the model is never called."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(_config(uncorrelated_tokens_per_minute=0), ports)
    request, ctx = run_request(BUDGET)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _in_run(ctx)) as record,
    ):
        res = await client.post(path, json={**ANTHROPIC_BODY, "model": RESOLVED})
    assert res.status_code == 403, res.text
    assert "uncorrelated_refused" in res.text
    assert model.calls == []
    assert (record.spent_tokens, record.reserved_tokens) == (0, 0)
    assert telemetry_of(ports).counter_value("chassis.model_calls_refused", route=DEFAULT) == 1
