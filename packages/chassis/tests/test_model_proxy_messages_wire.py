"""The Anthropic proxy route (proxy port, `POST /v1/messages`; contract v5, part A): the wire.

The body shape the real CLI sent, replayed over two turns of a tool round trip, then the complete
form, block indexes, and the turns the route skips or refuses. Driven in process, no socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from chassis.adapters.anthropic_compat import model_wire
from chassis.adapters.anthropic_compat.types import Message
from chassis.fakes import InMemoryTelemetry, ScriptedModel, ScriptRule
from chassis.ports.model import ModelMessage, ToolCallRequest
from chassis.server import ChassisConfig, create_app
from chassis.server.correlation import RunRecord
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    GLOSSARY_RULES,
    PROXY_CONFIG,
    TRACEPARENT,
    Frame,
    RecordingModel,
    asgi_client,
    frame_names,
    proxy_ports,
    run_request,
    sse_frames,
    telemetry_of,
    validate_stream_events,
)

CAPTURED: dict[str, Any] = {
    "model": "big-default",
    "max_tokens": 32000,
    "stream": True,
    "system": [
        {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.294; cch=abc;"},
        {
            "type": "text",
            "text": "You are a Claude agent.",
            "cache_control": {"type": "ephemeral"},
        },
    ],
    "messages": [
        {"role": "system", "content": [{"type": "text", "text": "# Environment\ncwd: /work"}]},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "lookup SLM",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
    ],
    "tools": [
        {
            "name": "mcp__glossary__glossary_lookup",
            "description": "Look up a term.",
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"term": {"type": "string"}},
                "required": ["term"],
            },
        }
    ],
    "thinking": {"type": "adaptive", "display": "updates"},
    "output_config": {"effort": "high"},
    "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
    "metadata": {"user_id": '{"device_id":"d","account_uuid":"a","session_id":"s"}'},
    "safeguards": {"context": {"cwd": "/home/someone/work"}},
}
"""The body shape the real CLI sent (`notes/capture`): `role: system` in `messages`, `system`
blocks with `cache_control`, `thinking`, `output_config`, `context_management`, `metadata`,
`safeguards`, and a tool with `$schema`."""

TOOL_RESULT_TURN: dict[str, Any] = {
    **CAPTURED,
    "messages": [
        CAPTURED["messages"][1],
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "mcp__glossary__glossary_lookup",
                    "input": {"term": "SLM"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": [{"type": "text", "text": "small language model"}],
                    "is_error": False,
                }
            ],
        },
    ],
}
"""Turn 2: the client posts the assistant `tool_use` and a user `tool_result`."""


@dataclass(frozen=True)
class RoundTrip:
    """Both turns of the captured body's tool round trip, and what the chassis saw."""

    turn1: httpx.Response
    turn2: httpx.Response
    frames1: list[Frame]
    frames2: list[Frame]
    model: RecordingModel
    record: RunRecord
    telemetry: InMemoryTelemetry


@pytest.fixture
async def round_trip() -> RoundTrip:
    model = RecordingModel(GLOSSARY_RULES)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)
    request, ctx = run_request(max_tokens=40000)
    headers = {"traceparent": TRACEPARENT, "anthropic-version": "2023-06-01"}
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        turn1 = await client.post("/v1/messages?beta=true", json=CAPTURED, headers=headers)
        turn2 = await client.post("/v1/messages?beta=true", json=TOOL_RESULT_TURN, headers=headers)
    return RoundTrip(
        turn1=turn1,
        turn2=turn2,
        frames1=sse_frames(turn1.text),
        frames2=sse_frames(turn2.text),
        model=model,
        record=record,
        telemetry=telemetry_of(ports),
    )


def test_the_captured_body_streams_sse_with_a_request_id_when_turn_1_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 1 is a 200 SSE stream, uncached, with a `req_` request id."""
    res = round_trip.turn1

    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["request-id"].startswith("req_")


def test_the_captured_body_sends_valid_frames_naming_big_default_when_turn_1_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 1 sends the seven frames of one tool block in order, the first naming the model."""
    frames = round_trip.frames1

    names = frame_names(frames)

    assert names == [
        "message_start",
        "ping",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ], names
    validate_stream_events(frames)
    assert frames[0][1]["message"]["model"] == "big-default"


def test_the_captured_body_streams_a_tool_use_block_with_usage_when_turn_1_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 1 streams the tool call as a `tool_use` block, its input as JSON, and the usage."""
    frames = round_trip.frames1

    start, delta, end = frames[2][1], frames[3][1], frames[5][1]

    assert start["content_block"] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "mcp__glossary__glossary_lookup",
        "input": {},
    }
    assert delta["delta"] == {"type": "input_json_delta", "partial_json": '{"term":"SLM"}'}
    assert end["delta"]["stop_reason"] == "tool_use"
    assert end["usage"] == {"input_tokens": 812, "output_tokens": 31}


def test_the_captured_body_reaches_the_model_as_plain_messages_when_turn_1_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 1 reaches the model as system and user text, without the billing header."""
    model = round_trip.model

    assert model.calls[0] == [
        ModelMessage(role="system", content="You are a Claude agent."),
        ModelMessage(role="system", content="# Environment\ncwd: /work"),
        ModelMessage(role="user", content="lookup SLM"),
    ]


def test_the_captured_body_sends_the_tool_schema_without_dollar_schema_when_turn_1_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 1 sends the tool's parameters without `$schema`, `max_tokens` 32000, temperature 0."""
    model = round_trip.model

    sent = model.tools[0]

    assert sent is not None and sent[0].parameters == {
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    }
    assert model.max_tokens[0] == 32000 and model.temperatures[0] == 0.0


def test_the_tool_result_turn_reaches_the_model_as_a_tool_message_when_turn_2_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 2 maps `tool_use` to an assistant tool call and `tool_result` to a tool message."""
    model = round_trip.model

    assert round_trip.turn2.status_code == 200, round_trip.turn2.text
    assert model.calls[1][-2:] == [
        ModelMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCallRequest(
                    call_id="call_1",
                    name="mcp__glossary__glossary_lookup",
                    arguments={"term": "SLM"},
                )
            ],
        ),
        ModelMessage(role="tool", tool_call_id="call_1", content="small language model"),
    ]


def test_the_tool_result_turn_ends_the_turn_and_both_calls_count_when_turn_2_is_posted(
    round_trip: RoundTrip,
) -> None:
    """Turn 2 ends with `end_turn`, and the run counts two model calls."""
    assert round_trip.frames2[-2][1]["delta"]["stop_reason"] == "end_turn"
    assert round_trip.record.model_calls == 2


@pytest.mark.parametrize(
    "param",
    [
        "thinking",
        "output_config.effort",
        "context_management",
        "safeguards",
        "system.billing_header",
    ],
)
def test_the_ignored_params_counter_names_a_param_when_the_captured_body_carries_it(
    round_trip: RoundTrip, param: str
) -> None:
    """Each ignored parameter the CLI sends is counted under its name."""
    telemetry = round_trip.telemetry

    assert telemetry.counter_value("chassis.model_proxy.ignored", format="anthropic", param=param)


def test_the_ignored_params_counter_skips_metadata_when_the_captured_body_carries_it(
    round_trip: RoundTrip,
) -> None:
    """`metadata` is dropped silently: no counter names it."""
    assert not any("metadata" in repr(k) for k in round_trip.telemetry.counters)


def test_the_model_call_span_names_the_anthropic_format_when_the_captured_body_is_posted(
    round_trip: RoundTrip,
) -> None:
    """The model call gets a span with `format: anthropic`."""
    assert any(
        s.name == "chassis.model.call" and s.attributes.get("format") == "anthropic"
        for s in round_trip.telemetry.spans
    )


async def test_the_complete_form_validates_as_a_message_when_stream_is_false() -> None:
    """A complete call returns an SDK-valid `Message` with the tool call and its usage."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )
    body = {**CAPTURED, "stream": False, "max_tokens": 64}  # 32000 is over the cap

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body, headers={"accept": "application/json"})
        plain = await client.post(
            "/v1/messages", json={**ANTHROPIC_BODY, "stream": True}, headers={}
        )

    assert res.status_code == 200, res.text
    Message.model_validate(res.json())
    assert res.json()["stop_reason"] == "tool_use"
    assert res.json()["content"] == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "mcp__glossary__glossary_lookup",
            "input": {"term": "SLM"},
        }
    ]
    assert res.json()["usage"] == {"input_tokens": 812, "output_tokens": 31}
    # accept does not pick the form: stream true is SSE, with or without an accept header.
    assert plain.headers["content-type"].startswith("text/event-stream")


async def test_the_stream_keeps_block_indexes_in_order_when_text_precedes_a_tool_call() -> None:
    """A text block then a tool block are indexes 0 and 1, and the stream ends cleanly."""
    model = ScriptedModel(
        [
            ScriptRule(
                match="go",
                reply="Looking. ",
                tool_call=ToolCallRequest(call_id="c9", name="t", arguments={"k": 1}),
            )
        ]
    )
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))
    body = {**ANTHROPIC_BODY, "messages": [{"role": "user", "content": "go"}], "stream": True}

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)

    frames = sse_frames(res.text)
    indexes = [d["index"] for n, d in frames if n == "content_block_start"]
    assert indexes == [0, 1]
    assert frame_names(frames)[-3:] == ["content_block_stop", "message_delta", "message_stop"]
    validate_stream_events(frames)


async def test_the_route_skips_or_refuses_empty_turns_and_never_500s_when_a_turn_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty system and thinking-only turns are skipped, an empty user turn is a 400, and an
    invalid parsed message is a 400 too."""
    model = RecordingModel(GLOSSARY_RULES)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)
    ok_body = {
        **ANTHROPIC_BODY,
        "system": [{"type": "text", "text": ""}],
        "messages": [
            {"role": "system", "content": ""},
            {"role": "user", "content": "simplify: a"},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]},
            {"role": "user", "content": "simplify: b"},
        ],
    }
    empty_user = {**ANTHROPIC_BODY, "messages": [{"role": "user", "content": []}]}

    def invalid(raw: Any) -> Any:
        ModelMessage(role="tool")
        raise AssertionError("unreachable")

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        ok = await client.post("/v1/messages", json=ok_body)
        refused = await client.post("/v1/messages", json=empty_user)
        monkeypatch.setattr(model_wire, "_parse", invalid)
        broken = await client.post("/v1/messages", json=ANTHROPIC_BODY)

    assert ok.status_code == 200, ok.text
    assert model.calls[0] == [
        ModelMessage(role="user", content="simplify: a"),
        ModelMessage(role="user", content="simplify: b"),
    ]
    assert refused.status_code == 400
    assert refused.json()["error"] == {
        "type": "invalid_request_error",
        "message": "invalid_body: messages[0].content: a user turn needs content",
    }
    assert broken.status_code == 400, broken.text
    assert telemetry_of(ports).counter_value("chassis.model_calls", route="big-default") == 1
