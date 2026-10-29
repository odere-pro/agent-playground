"""The Anthropic proxy route (proxy port, `POST /v1/messages`): the body limits.

The 4 MiB body cap, deep nesting, the model name length, and JSON booleans in numeric fields.
Each is refused before the model is called. Driven in process, no socket.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from chassis.adapters.anthropic_compat.model_wire import BODY_CAP_BYTES
from chassis.server import ChassisConfig, create_app
from chassis.server import model_proxy_messages as route_module
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import (
    ANTHROPIC_BODY,
    GLOSSARY_RULES,
    PROXY_CONFIG,
    RecordingModel,
    asgi_client,
    proxy_ports,
    telemetry_of,
)

PIECE = b"x" * (256 * 1024)


def test_the_body_cap_is_four_mebibytes_when_read_from_the_wire_module() -> None:
    """The cap is 4 MiB."""
    assert BODY_CAP_BYTES == 4 * 1024 * 1024


@pytest.fixture
def must_not_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if the route parses a body."""

    def refuse(raw: Any) -> Any:
        raise AssertionError("parsed an oversized body")

    monkeypatch.setattr(route_module, "parse_messages_request", refuse)
    monkeypatch.setattr(route_module, "json", SimpleNamespace(loads=refuse))


@pytest.mark.usefixtures("must_not_parse")
async def test_the_route_stops_reading_and_answers_413_when_a_chunked_body_is_over_the_cap() -> (
    None
):
    """A chunked body with no `Content-Length` is refused while it is read, before parsing."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )
    total = BODY_CAP_BYTES // len(PIECE) * 4
    read = 0

    async def chunked() -> AsyncIterator[bytes]:
        nonlocal read
        for _ in range(total):
            read += 1
            yield PIECE

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", content=chunked())

    assert res.status_code == 413, res.text
    assert res.json()["error"]["type"] == "request_too_large"
    assert res.json()["type"] == "error" and res.headers["request-id"].startswith("req_")
    assert read <= BODY_CAP_BYTES // len(PIECE) + 2, f"read {read} of {total} pieces"


@pytest.mark.usefixtures("must_not_parse")
async def test_the_route_answers_413_unparsed_when_a_declared_body_is_over_the_cap() -> None:
    """A body one byte over the cap, with its `Content-Length`, is refused before parsing."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", content=b"x" * (BODY_CAP_BYTES + 1))

    assert res.status_code == 413, res.text
    assert res.json()["error"]["type"] == "request_too_large"
    assert res.json()["type"] == "error" and res.headers["request-id"].startswith("req_")


async def test_the_route_parses_the_body_when_it_is_within_the_cap() -> None:
    """A small body is read and parsed: not JSON is a 400 `invalid_body`, not a 413."""
    app = create_app(
        ChassisConfig.model_validate(PROXY_CONFIG), proxy_ports(RecordingModel(GLOSSARY_RULES))
    )

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", content=b"not json")

    assert res.status_code == 400
    assert res.json()["error"]["message"].startswith("invalid_body: ")


@pytest.mark.parametrize("body", [b"[" * 100_000, b'{"a":' * 100_000])
async def test_the_route_answers_400_and_never_500_when_the_body_is_deeply_nested(
    body: bytes,
) -> None:
    """A deeply nested body is a 400 `invalid_body` and calls no model."""
    ports = proxy_ports(RecordingModel(GLOSSARY_RULES))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)

    async with (
        asgi_client(create_proxy_app(app), raise_app_exceptions=False) as client,
        app.router.lifespan_context(app),
    ):
        res = await client.post("/v1/messages", content=body)

    assert res.status_code == 400, res.text[:200]
    assert res.json()["error"] == {
        "type": "invalid_request_error",
        "message": "invalid_body: body: the body is not valid JSON",
    }
    assert telemetry_of(ports).counter_value("chassis.model_calls", route="big-default") == 0


async def test_the_route_refuses_before_admission_and_records_nothing_when_the_model_name_is_long(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A model name over 256 characters is a short 400 and reaches no counter, log, or span."""
    model = RecordingModel(GLOSSARY_RULES)
    ports = proxy_ports(model)
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)
    caplog.set_level(logging.DEBUG)

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post(
            "/v1/messages", json={**ANTHROPIC_BODY, "model": "m" * (3 * 1024 * 1024)}
        )
        edge = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "model": "e" * 256})
        over = await client.post("/v1/messages", json={**ANTHROPIC_BODY, "model": "o" * 257})

    assert res.status_code == 400 and len(res.text) < 500
    assert res.json()["error"]["message"] == "invalid_body: model: at most 256 characters"
    assert (edge.status_code, over.status_code) == (200, 400)
    telemetry = telemetry_of(ports)
    surfaces = repr(telemetry.counters) + repr(telemetry.logs) + repr(telemetry.spans) + caplog.text
    assert "mmmmmmmm" not in surfaces and "ooooooo" not in surfaces
    assert len(model.calls) == 1


@pytest.mark.parametrize("key", ["max_tokens", "temperature"])
async def test_the_route_answers_400_without_a_model_call_when_a_number_is_a_json_boolean(
    key: str,
) -> None:
    """`true` in `max_tokens` or `temperature` is a 400 naming the field."""
    ports = proxy_ports(RecordingModel(GLOSSARY_RULES))
    app = create_app(ChassisConfig.model_validate(PROXY_CONFIG), ports)

    async with asgi_client(create_proxy_app(app)) as client, app.router.lifespan_context(app):
        res = await client.post("/v1/messages", json={**ANTHROPIC_BODY, key: True})

    assert res.status_code == 400
    assert res.json()["error"]["message"].startswith(f"invalid_body: {key}: ")
    assert telemetry_of(ports).counter_value("chassis.model_calls", route="big-default") == 0
