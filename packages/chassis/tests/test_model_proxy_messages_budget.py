"""The Anthropic proxy route (proxy port, `POST /v1/messages`): admission and charging.

A correlated call is charged to its run and clamped to what is left, an exhausted run is a 429,
the uncorrelated cap is shared with the chat route, and an abandoned stream is charged (this route
only; the chat route's gap is in `test_model_proxy.py`). Driven in process, no socket.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, Usage
from chassis.server import ChassisConfig, create_app
from chassis.server import model_proxy_messages as route_module
from chassis.server.model_proxy_messages import FIRST_CHUNK_WAIT_S
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    GLOSSARY_RULES,
    PROXY_CONFIG,
    TRACEPARENT,
    GatedModel,
    RecordingModel,
    abandon_stream,
    asgi_client,
    proxy_ports,
    run_request,
    telemetry_of,
)

STREAM_30 = {**ANTHROPIC_BODY, "max_tokens": 30, "stream": True}


class HoldPhaseBugModel:
    """A chassis bug in the hold phase: `stream` raises a non-`ModelError`."""

    name = "bug"

    def __init__(self, at_call: bool) -> None:
        self.at_call = at_call

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        raise RuntimeError("bug")

    def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[ModelChunk]:
        if self.at_call:
            raise RuntimeError("bug")  # before the upstream call exists

        async def gen() -> AsyncIterator[ModelChunk]:
            raise RuntimeError("bug")
            yield ModelChunk()

        return gen()


async def test_an_abandoned_stream_is_charged_its_reservation_when_usage_is_unknown() -> None:
    """A client that leaves before any usage is known is charged the full `max_tokens`."""
    model = GatedModel()

    record = await abandon_stream(model, "/v1/messages", STREAM_30, b"text_delta")

    assert model.max_tokens == [30]
    assert (record.spent_tokens, record.reserved_tokens) == (30, 0)


async def test_an_abandoned_stream_is_charged_its_last_known_usage_when_usage_was_reported() -> (
    None
):
    """A client that leaves after the model reported usage is charged that usage."""
    model = GatedModel(first_usage=Usage(input_tokens=3, output_tokens=4))

    record = await abandon_stream(model, "/v1/messages", STREAM_30, b"text_delta")

    assert (record.spent_tokens, record.reserved_tokens) == (7, 0)


async def test_an_abandoned_stream_is_charged_its_reservation_when_the_model_was_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model silent past the hold window, then a client that leaves in the body phase, is
    charged the reservation.

    It does not prove a leave inside the window. The route does not read `receive` there, so a
    client that leaves during the hold is seen only at the first send, after the window (contract
    v5, A.6 step 9). That case cannot be observed, so it is not tested.
    """
    monkeypatch.setattr(route_module, "FIRST_CHUNK_WAIT_S", 0.05)
    model = GatedModel(silent=True)  # no chunk before the gate: the window passes

    record = await abandon_stream(model, "/v1/messages", STREAM_30, b"message_start")

    assert (record.spent_tokens, record.reserved_tokens) == (30, 0)


def test_the_hold_window_and_ping_interval_match_the_contract_when_read() -> None:
    """The first-chunk hold is 5 s and the ping interval 15 s (contract v5, A.6)."""
    assert FIRST_CHUNK_WAIT_S == 5.0 and route_module.PING_INTERVAL_S == 15.0


async def test_a_correlated_call_is_clamped_to_what_is_left_when_it_asks_for_more() -> None:
    """A call asking 32000 of a 100-token run forwards 100, is charged 15, and the next runs."""
    model = RecordingModel(GLOSSARY_RULES)
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))
    request, ctx = run_request(max_tokens=100)
    headers = {"traceparent": TRACEPARENT}

    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post(
            "/v1/messages", json={**ANTHROPIC_BODY, "max_tokens": 32000}, headers=headers
        )
        spent = record.spent_tokens
        again = await client.post("/v1/messages", json=ANTHROPIC_BODY, headers=headers)

    assert res.status_code == 200
    assert model.max_tokens[0] == 100 and spent == 15
    assert again.status_code == 200 and record.model_calls == 2


@pytest.mark.parametrize("stream", [False, True])
async def test_the_route_answers_a_429_json_body_when_the_run_is_exhausted(stream: bool) -> None:
    """A run with nothing left is a JSON 429 `rate_limit_error`, streamed or not, and counted."""
    ports = proxy_ports(RecordingModel(GLOSSARY_RULES))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)
    request, ctx = run_request(max_tokens=15)
    headers = {"traceparent": TRACEPARENT}

    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx),
    ):
        first = await client.post("/v1/messages", json=ANTHROPIC_BODY, headers=headers)
        res = await client.post(
            "/v1/messages", json={**ANTHROPIC_BODY, "stream": stream}, headers=headers
        )

    assert first.status_code == 200
    assert res.status_code == 429
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["error"]["type"] == "rate_limit_error"
    assert res.json()["error"]["message"].startswith("budget_exhausted: ")
    assert res.headers["x-should-retry"] == "false"
    assert (
        telemetry_of(ports).counter_value("chassis.model_calls_refused", route="big-default") == 1
    )


async def test_the_uncorrelated_cap_refuses_both_routes_when_their_shared_spend_is_over_it() -> (
    None
):
    """Chat and messages calls draw on one uncorrelated cap of 100 tokens a minute."""
    config = {
        **PROXY_CONFIG,
        "spec": {**PROXY_CONFIG["spec"], "limits": {"uncorrelated_tokens_per_minute": 100}},
    }
    app = create_app(
        ChassisConfig.model_validate(config), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        chat = await client.post("/v1/chat/completions", json={**ANTHROPIC_BODY, "max_tokens": 60})
        messages = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "max_tokens": 60})
    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        refused = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "max_tokens": 90})
        back = await client.post("/v1/chat/completions", json={**ANTHROPIC_BODY, "max_tokens": 90})

    assert chat.status_code == 200
    # 15 spent on chat, 60 asked here: 75 fits; the cap is 100 per minute, so ask for more.
    assert messages.status_code == 200
    assert refused.status_code == 429 and back.status_code == 429
    assert refused.json()["error"]["message"].startswith("budget_exhausted: ")


@pytest.mark.parametrize("at_call", [True, False])
async def test_the_run_is_charged_nothing_when_a_chassis_bug_fails_the_hold_phase(
    at_call: bool,
) -> None:
    """A non-`ModelError` before the first chunk is a 500 and releases the reservation."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(HoldPhaseBugModel(at_call))
    )
    request, ctx = run_request(max_tokens=1000)

    async with (
        asgi_client(create_proxy_app(app), raise_app_exceptions=False) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        res = await client.post(
            "/v1/messages", json=STREAM_30, headers={"traceparent": TRACEPARENT}
        )

    assert res.status_code == 500
    assert (record.spent_tokens, record.reserved_tokens) == (0, 0)
