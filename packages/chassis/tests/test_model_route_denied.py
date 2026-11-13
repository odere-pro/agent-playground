"""012 H-3, admission box: "Admission enforces the resolved route and refuses another with 403
`model_route_denied`" (user decision, 2026-11-13: refuse, never redirect).

The only route a call of a run may name is the run's own, `RunRecord.model_route`: the task's route
through `spec.model.routes`, else `spec.model.route`, or the gate's fallback route for its fallback
attempt. Any other route is refused at admission with 403 `model_route_denied`, a route the config
lists included, before any budget is held and before the model is called, on both model proxy
routes, streaming or not. The run's own route passes even after a reload drops it from the
config. A run record with no route fails closed. The body is the fixed text of the upstream
refusal: no route, no key. Offline.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from chassis.core.envelope import Context
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import RunRecord
from chassis.server.model_admission import ROUTE_REFUSED, ROUTE_SPAN, UNRESOLVED
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    PLAIN_WORDS,
    TRACEPARENT,
    RecordingModel,
    asgi_client,
    proxy_ports,
    run_request,
    telemetry_of,
)

LISTED = ("big-default", "small-default", "lookup-default", "fallback-route", "decision-small")
CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {
            "route": "big-default",
            "fallback_route": "fallback-route",
            "judge_route": "decision-small",
            "routes": {"simplify": "small-default", "lookup": "lookup-default"},
        },
    },
}
RESOLVED = "small-default"
"""The run's route: the `simplify` task's route through `spec.model.routes`."""
UNLISTED = "gpt-unlisted"
OTHERS = [UNLISTED, "big-default", "fallback-route", "decision-small"]
"""Routes that are not the run's: one the config does not list, and three it does."""
DENIED_TEXT = "this model route is not allowed for this service"
CHAT_DENIED = {
    "error": {
        "message": DENIED_TEXT,
        "type": "permission_error",
        "code": "model_route_denied",
        "retryable": False,
    }
}
CHAT = "/v1/chat/completions"
MESSAGES = "/v1/messages"
PATHS = [CHAT, MESSAGES]
BUDGET = 1000


def _run(ctx: Context, route: str | None = RESOLVED) -> Context:
    return ctx.model_copy(update={"model_route": route})


async def _call_in_run(
    path: str, route: str, *, stream: bool, run_route: str | None = RESOLVED
) -> tuple[int, str, Any, RecordingModel, RunRecord]:
    """One call of a run whose resolved route is `run_route`, naming `route`."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(BUDGET)
    body = {**ANTHROPIC_BODY, "model": route, "stream": stream}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _run(ctx, run_route)) as record,
    ):
        res = await client.post(path, json=body, headers={"traceparent": TRACEPARENT})
        held = record.reserved_tokens
    assert held == 0, "the call holds nothing once it is answered"
    return res.status_code, res.text, telemetry_of(ports), model, record


def _assert_nothing_charged(record: RunRecord, telemetry: Any) -> None:
    """A refusal at admission holds, spends, and counts no budget."""
    assert (record.spent_tokens, record.reserved_tokens) == (0, 0)
    assert record.remaining_tokens == BUDGET
    for route in (RESOLVED, UNLISTED, UNRESOLVED, *LISTED):
        assert telemetry.counter_value("chassis.model_calls_refused", route=route) == 0


def _chat_stream_body(text: str) -> dict[str, Any]:
    frames = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    assert frames[-1] == "[DONE]", frames
    (body,) = [json.loads(f) for f in frames[:-1]]
    assert isinstance(body, dict)
    return body


def _assert_denied(path: str, text: str, *, stream: bool) -> None:
    if path == CHAT:
        body = _chat_stream_body(text) if stream else json.loads(text)
        assert body == CHAT_DENIED
        return
    body = json.loads(text)
    assert body["type"] == "error", body
    assert body["error"] == {
        "type": "permission_error",
        "message": f"model_route_denied: {DENIED_TEXT}",
    }


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("path", PATHS)
async def test_a_call_on_the_resolved_route_passes(path: str, stream: bool) -> None:
    status, text, telemetry, model, record = await _call_in_run(path, RESOLVED, stream=stream)
    assert status == 200, text
    assert "words." in text
    assert len(model.calls) == 1
    assert record.spent_tokens > 0
    assert telemetry.counter_value(ROUTE_REFUSED, route=RESOLVED) == 0
    (span,) = [s for s in telemetry.spans if s.name == ROUTE_SPAN]
    assert (span.attributes["route"], span.attributes["route.forced"]) == (RESOLVED, False)


@pytest.mark.parametrize("route", OTHERS)
@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("path", PATHS)
async def test_another_route_is_403_model_route_denied(path: str, stream: bool, route: str) -> None:
    """Any route but the run's, listed in the config or not, is refused and never redirected."""
    status, text, telemetry, model, record = await _call_in_run(path, route, stream=stream)
    assert status == 403, text
    _assert_denied(path, text, stream=stream)
    assert model.calls == [], "refused at admission: the model is never called"
    _assert_nothing_charged(record, telemetry)
    assert telemetry.counter_value(ROUTE_REFUSED, route=RESOLVED) == 1
    assert not [s for s in telemetry.spans if s.name == ROUTE_SPAN], "no routing choice is made"
    # The body names no route: neither the one asked for, nor any the service lists, nor a key.
    for name in (UNLISTED, *LISTED, "sk-", "Bearer"):
        assert name not in text, name


@pytest.mark.parametrize("path", PATHS)
async def test_a_run_keeps_its_own_route_after_a_reload_drops_it(path: str) -> None:
    """`docs/specs/models.md`: a run keeps the route it opened with. A reload that drops the
    run's route from the config does not refuse the run's later calls on it."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(BUDGET)
    document = json.loads(json.dumps({**CONFIG, "version": "cfg-2"}))
    document["spec"]["model"]["routes"] = {"lookup": "lookup-default"}
    body = {**ANTHROPIC_BODY, "model": RESOLVED}
    headers = {"traceparent": TRACEPARENT}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _run(ctx)),
    ):
        before = await client.post(path, json=body, headers=headers)
        await app.state.ports.config.put("echo", document)
        dropped = app.state.config.spec.model.declared_routes()
        after = await client.post(path, json=body, headers=headers)
    assert RESOLVED not in dropped, dropped
    assert (before.status_code, after.status_code) == (200, 200), after.text
    assert len(model.calls) == 2
    assert telemetry_of(ports).counter_value(ROUTE_REFUSED, route=RESOLVED) == 0


@pytest.mark.parametrize("route", [UNLISTED, "big-default"])
@pytest.mark.parametrize("path", PATHS)
async def test_a_run_with_no_resolved_route_refuses_every_route(path: str, route: str) -> None:
    """Fail closed: a run record with no `model_route` lets no route through, listed or not."""
    status, text, telemetry, model, record = await _call_in_run(
        path, route, stream=False, run_route=None
    )
    assert status == 403, text
    assert model.calls == []
    _assert_nothing_charged(record, telemetry)
    assert telemetry.counter_value(ROUTE_REFUSED, route=UNRESOLVED) == 1


@pytest.mark.parametrize("path", PATHS)
async def test_the_gates_fallback_route_is_the_runs_route_for_its_attempt(path: str) -> None:
    """The gate sets `RunRecord.model_route` (and `ctx.model_route`) to the fallback route for its
    fallback attempt: a call on it passes, a call on the run's first route is refused."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(BUDGET)
    headers = {"traceparent": TRACEPARENT}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _run(ctx)) as record,
    ):
        record.model_route = "fallback-route"
        fallback = await client.post(
            path, json={**ANTHROPIC_BODY, "model": "fallback-route"}, headers=headers
        )
        first = await client.post(path, json={**ANTHROPIC_BODY, "model": RESOLVED}, headers=headers)
    assert fallback.status_code == 200, fallback.text
    assert first.status_code == 403, first.text
    assert len(model.calls) == 1
