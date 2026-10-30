"""The Anthropic proxy route (proxy port, `POST /v1/messages`): errors in the Anthropic shape.

Model errors send fixed text and keep the upstream text in the log, an upstream 401 or 403 is a
403 `model_route_denied`, and the route's own refusals (not ready, an orphan `tool_result`,
`count_tokens`) never echo the request. Driven in process, no socket.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from chassis.ports.model import ModelChunk, ModelError, ModelMessage
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    GLOSSARY_RULES,
    PROXY_CONFIG,
    FailingModel,
    RecordingModel,
    asgi_client,
    frame_names,
    proxy_ports,
    sse_frames,
    telemetry_of,
)

UPSTREAM_TEXT = "context length 99999 exceeded (upstream-detail-marker)"


class LateFailingModel:
    """Streams one text chunk, then raises a `ModelError` with the upstream text."""

    name = "late"

    async def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[Any]:
        yield ModelChunk(text="partial")
        raise ModelError("http_400", UPSTREAM_TEXT)


@pytest.mark.parametrize("stream", [False, True])
async def test_the_route_sends_fixed_text_and_logs_the_upstream_text_when_the_model_rejects(
    stream: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """A model 400 is a 400 with fixed text; only the log holds the upstream text."""
    model = FailingModel(ModelError("http_400", UPSTREAM_TEXT))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))
    caplog.set_level(logging.DEBUG)

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "stream": stream})

    assert res.status_code == 400, res.text
    assert "upstream-detail-marker" not in res.text and "99999" not in res.text
    assert res.json()["error"] == {
        "type": "invalid_request_error",
        "message": "model_rejected_request: the model route rejected the request",
    }
    assert res.headers["x-should-retry"] == "false"
    assert "upstream-detail-marker" in caplog.text  # the operator still sees it


async def test_the_stream_ends_with_a_fixed_text_error_frame_when_the_model_fails_mid_stream() -> (
    None
):
    """A model error after the first chunk ends the stream with an `error` frame, no stop."""
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(LateFailingModel()))

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "stream": True})

    assert res.status_code == 200
    frames = sse_frames(res.text)
    assert frames[-1][0] == "error" and "message_stop" not in frame_names(frames)
    assert "upstream-detail-marker" not in res.text
    assert frames[-1][1]["error"]["message"].startswith("model_rejected_request: ")


@pytest.mark.parametrize("code", ["http_401", "http_403"])
@pytest.mark.parametrize("stream", [False, True])
async def test_the_route_answers_403_model_route_denied_when_the_upstream_answers_401_or_403(
    code: str, stream: bool
) -> None:
    """An upstream 401 or 403 is a 403 `permission_error`, counted, with no key echoed."""
    ports = proxy_ports(FailingModel(ModelError(code, "Received API Key = sk-abcdef12345678")))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "stream": stream})

    assert res.status_code == 403, res.text
    assert res.json()["error"] == {
        "type": "permission_error",
        "message": "model_route_denied: this model route is not allowed for this service",
    }
    assert "sk-abcdef" not in res.text
    assert (
        telemetry_of(ports).counter_value("chassis.model_upstream_denied", route="big-default") == 1
    )


@pytest.mark.parametrize(
    ("code", "retryable", "status", "kind", "named"),
    [
        ("http_429", True, 429, "rate_limit_error", "model_rate_limited"),
        ("http_503", True, 503, "overloaded_error", "model_overloaded"),
        ("http_529", True, 503, "overloaded_error", "model_overloaded"),
        ("timeout", True, 504, "timeout_error", "model_timeout"),
        ("http_500", True, 502, "api_error", "model_unavailable"),
        ("bad_response", False, 500, "api_error", "model_error"),
    ],
)
async def test_the_route_sends_the_rows_status_and_fixed_text_when_the_model_errors(
    code: str, retryable: bool, status: int, kind: str, named: str
) -> None:
    """Each model error code maps to its status, Anthropic type, named code, and retry header."""
    model = FailingModel(ModelError(code, "UPSTREAM-RAW-TEXT", retryable=retryable))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(model))

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=ANTHROPIC_BODY)

    assert res.status_code == status
    assert res.json()["error"]["type"] == kind
    assert res.json()["error"]["message"].startswith(f"{named}: ")
    assert "UPSTREAM-RAW-TEXT" not in res.text
    assert res.headers["x-should-retry"] == ("true" if retryable else "false")


async def test_the_route_answers_503_overloaded_when_the_chassis_is_not_ready() -> None:
    """Before the lifespan starts, the route is a retryable 503 `overloaded_error`."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )

    async with asgi_client(create_proxy_app(app)) as client:
        res = await client.post("/v1/messages", json=ANTHROPIC_BODY)

    assert res.status_code == 503
    assert res.json()["error"]["type"] == "overloaded_error"
    assert res.headers["x-should-retry"] == "true"


async def test_the_route_answers_400_without_echo_when_a_tool_result_has_no_tool_use() -> None:
    """A `tool_result` with no preceding `tool_use` is `unsupported_message`, its id not echoed."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )
    body = {
        **ANTHROPIC_BODY,
        "messages": [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "nope-1"}]}
        ],
    }

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json=body)

    assert res.status_code == 400 and "nope-1" not in res.text
    assert res.json()["error"]["message"].startswith("unsupported_message: ")


@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_count_tokens_answers_404_in_the_anthropic_shape_when_called(method: str) -> None:
    """`count_tokens` is a 404 `not_found_error`; every other 404 keeps FastAPI's body."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.request(method, "/v1/messages/count_tokens", json=ANTHROPIC_BODY)
        other = await client.post("/v1/models", json={})

    assert res.status_code == 404
    assert res.json()["type"] == "error"
    assert res.json()["error"]["type"] == "not_found_error"
    assert res.headers["request-id"] == res.json()["request_id"]
    assert other.json() == {"detail": "Not Found"}
