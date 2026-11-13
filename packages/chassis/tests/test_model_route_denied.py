"""012 H-3, admission box: "Admission enforces the resolved route and refuses another with 403
`model_route_denied`" (user decision, 2026-11-13: refuse, never rewrite).

The service's routes are the ones its config names: `spec.model.route`, `fallback_route`,
`judge_route`, and every value of `routes`. The chassis has no offline copy of the LiteLLM key's
list, so the config stands in for it (`docs/specs/models.md`). A call of a run that names a route
outside that list is refused at admission with 403 `model_route_denied`, before any budget is
held and before the model is called, on both model proxy routes, streaming or not. A listed route
still goes to the run's resolved route (`chassis.model_route_forced`), so the gate's fallback
attempt keeps working. The body is the fixed text of the upstream refusal: no route list, no key.
Offline.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from chassis.core.envelope import Context
from chassis.server import ChassisConfig, create_app
from chassis.server.model_admission import FORCED, ROUTE_REFUSED
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


def _run(ctx: Context) -> Context:
    return ctx.model_copy(update={"model_route": RESOLVED})


async def _call(path: str, route: str, *, stream: bool) -> tuple[int, str, Any, RecordingModel]:
    """One call of a run whose resolved route is `RESOLVED`, naming `route`."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(1000)
    body = {**ANTHROPIC_BODY, "model": route, "stream": stream}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _run(ctx)) as record,
    ):
        res = await client.post(path, json=body, headers={"traceparent": TRACEPARENT})
        held = record.reserved_tokens
    assert held == 0, "the call holds nothing once it is answered"
    return res.status_code, res.text, telemetry_of(ports), model


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("path", PATHS)
async def test_a_call_on_the_resolved_route_passes(path: str, stream: bool) -> None:
    status, text, telemetry, model = await _call(path, RESOLVED, stream=stream)
    assert status == 200, text
    assert "words." in text
    assert len(model.calls) == 1
    assert telemetry.counter_value(FORCED, route=RESOLVED) == 0
    assert telemetry.counter_value(ROUTE_REFUSED, route=RESOLVED) == 0


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("path", PATHS)
async def test_a_listed_route_that_is_not_the_runs_goes_to_the_resolved_route(
    path: str, stream: bool
) -> None:
    """A route the service lists is held to the run's route, not refused: the gate's fallback
    attempt relies on this."""
    status, text, telemetry, model = await _call(path, "big-default", stream=stream)
    assert status == 200, text
    assert len(model.calls) == 1
    assert telemetry.counter_value(FORCED, route=RESOLVED) == 1


def _chat_stream_body(text: str) -> dict[str, Any]:
    frames = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    assert frames[-1] == "[DONE]", frames
    (body,) = [json.loads(f) for f in frames[:-1]]
    assert isinstance(body, dict)
    return body


def _messages_denied(text: str) -> dict[str, Any]:
    body = json.loads(text)
    assert isinstance(body, dict)
    assert body["type"] == "error", body
    assert body["error"] == {
        "type": "permission_error",
        "message": f"model_route_denied: {DENIED_TEXT}",
    }
    return body


@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
@pytest.mark.parametrize("path", PATHS)
async def test_a_route_the_service_does_not_list_is_403_model_route_denied(
    path: str, stream: bool
) -> None:
    status, text, telemetry, model = await _call(path, UNLISTED, stream=stream)
    assert status == 403, text
    if path == CHAT:
        body = _chat_stream_body(text) if stream else json.loads(text)
        assert body == CHAT_DENIED
    else:
        _messages_denied(text)
    assert model.calls == [], "refused at admission: the model is never called"
    assert telemetry.counter_value(ROUTE_REFUSED, route=RESOLVED) == 1
    assert telemetry.counter_value(FORCED, route=RESOLVED) == 0
    # The body names no route: neither the one asked for, nor any the service lists, nor a key.
    for name in (UNLISTED, *LISTED, "sk-", "Bearer"):
        assert name not in text, name


async def test_the_listed_routes_follow_a_reload_with_no_restart() -> None:
    """The list is read per call from the live config: a route added by a reload passes."""
    model = RecordingModel(PLAIN_WORDS)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(1000)
    document = json.loads(json.dumps({**CONFIG, "version": "cfg-2"}))
    document["spec"]["model"]["routes"]["extra"] = UNLISTED
    body = {**ANTHROPIC_BODY, "model": UNLISTED}
    headers = {"traceparent": TRACEPARENT}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, _run(ctx)),
    ):
        before = await client.post(CHAT, json=body, headers=headers)
        await app.state.ports.config.put("echo", document)
        after = await client.post(CHAT, json=body, headers=headers)
    assert before.status_code == 403, before.text
    assert after.status_code == 200, after.text
    assert telemetry_of(ports).counter_value(FORCED, route=RESOLVED) == 1
