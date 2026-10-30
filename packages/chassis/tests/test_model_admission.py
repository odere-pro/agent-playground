"""The shared admission layer (`chassis.server.model_admission`): `read_capped` reads any declared
length safely, and a hold is given back when the call fails before or while it starts.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import pytest
from chassis.fakes import InMemoryTelemetry
from chassis.ports.bundle import PortBundle
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, Usage
from chassis.ports.telemetry import Span
from chassis.server import ChassisConfig, create_app
from chassis.server.model_admission import SPAN, read_capped
from chassis.server.proxy_app import create_proxy_app
from proxy_fakes import TRACEPARENT, asgi_client, proxy_ports, run_request
from starlette.requests import Request as HTTPRequest
from starlette.types import Message

CONFIG: dict[str, Any] = {
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {"engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}},
}
CHAT = {"model": "big-default", "max_tokens": 64, "messages": [{"role": "user", "content": "x"}]}
ROUTES = {"openai": "/v1/chat/completions", "anthropic": "/v1/messages"}


def _request(content_length: bytes, body: bytes) -> HTTPRequest:
    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "headers": [(b"content-length", content_length)]}
    return HTTPRequest(scope, receive)


@pytest.mark.parametrize("declared", [b"\xb2", b"\xb9", b"12x"], ids=["sup2", "sup1", "text"])
async def test_read_capped_reads_the_body_when_the_declared_length_is_not_an_ascii_number(
    declared: bytes,
) -> None:
    """A Latin-1 superscript digit passes `str.isdigit` but not `int`; it is read, not a 500."""
    assert await read_capped(_request(declared, b"{}")) == b"{}"


class SpanFailingTelemetry(InMemoryTelemetry):
    """Raises when the model call's span opens; every other span works."""

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        if name == SPAN:
            raise RuntimeError("span exporter down")
        with super().span(name, **attributes) as span:
            yield span


class StreamSetupFails:
    """`stream` raises when it is called, before any chunk: a bug while the call is set up."""

    name = "stream-setup-fails"

    async def complete(self, messages: Sequence[ModelMessage], **_: Any) -> ModelResult:
        return ModelResult(text="ok", usage=Usage(input_tokens=1, output_tokens=1))

    def stream(self, messages: Sequence[ModelMessage], **_: Any) -> AsyncIterator[ModelChunk]:
        raise RuntimeError("stream setup failed")


async def _post_and_fail(ports: PortBundle, path: str, body: dict[str, Any]) -> int:
    """Post one correlated call that raises inside the app; answer the run's tokens still held."""
    app = create_app(ChassisConfig.model_validate(CONFIG), ports)
    request, ctx = run_request(max_tokens=100)
    async with (
        asgi_client(create_proxy_app(app)) as client,
        app.router.lifespan_context(app),
        app.state.runs.register(request, ctx) as record,
    ):
        with pytest.raises(RuntimeError):
            await client.post(path, json=body, headers={"traceparent": TRACEPARENT})
        return record.reserved_tokens


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_complete_call_gives_back_its_hold_when_the_span_fails_to_open(route: str) -> None:
    """The hold is the outer block, so a span that fails to open still settles it."""
    ports = dataclasses.replace(proxy_ports(StreamSetupFails()), telemetry=SpanFailingTelemetry())

    assert await _post_and_fail(ports, ROUTES[route], CHAT) == 0


@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_stream_call_gives_back_its_hold_when_the_stream_setup_raises(route: str) -> None:
    """A bug between admission and the response gives the reservation back."""
    body = {**CHAT, "stream": True}

    assert await _post_and_fail(proxy_ports(StreamSetupFails()), ROUTES[route], body) == 0
