"""PoC-8 (023 C-5, ADR-008 item 7): what PoC-7 left of the marking.

- A body field in the OpenAI and Anthropic formats: `ai_marking: {"ai_generated": true,
  "disclosure_required": true}` (suggested name) on the completion and on every chunk (OpenAI),
  on the message and on `message_start` (Anthropic). Not there with marking off.
- The CloudEvent extension attribute `aigenerated: true` on result events.
- `Access-Control-Expose-Headers` names the two marking headers, so a browser client can read them.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from fastapi import FastAPI

MARK = {"ai_generated": True, "disclosure_required": True}
OPENAI = (
    "/v1/chat/completions",
    {"model": "echo", "messages": [{"role": "user", "content": "hi"}]},
)
ANTHROPIC = (
    "/v1/messages",
    {"model": "echo", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
)


def _app(risk: str = "limited", *, events: InMemoryBus | None = None) -> FastAPI:
    spec: dict[str, Any] = {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "governance": {"risk_class": risk},
        "events": {"result_events": events is not None},
    }
    config = ChassisConfig.model_validate(
        {"version": "c", "profile": "fake", "agent": {"name": "echo", "version": "1"}, "spec": spec}
    )
    extra: dict[str, Any] = {} if events is None else {"events": events}
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        **extra,
    )
    return create_app(config, ports)


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c") as client,
    ):
        yield client


def _data(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


async def test_the_openai_body_carries_the_flags_complete_and_on_every_chunk() -> None:
    path, body = OPENAI
    async with _client(_app()) as client:
        complete = await client.post(path, json=body)
        streamed = await client.post(path, json={**body, "stream": True})
    assert complete.json()["ai_marking"] == MARK
    chunks = _data(streamed.text)
    assert chunks
    assert all(chunk["ai_marking"] == MARK for chunk in chunks)


async def test_the_anthropic_body_carries_the_flags_complete_and_on_message_start() -> None:
    path, body = ANTHROPIC
    async with _client(_app()) as client:
        complete = await client.post(path, json=body)
        streamed = await client.post(path, json={**body, "stream": True})
    assert complete.json()["ai_marking"] == MARK
    start = next(e for e in _data(streamed.text) if e["type"] == "message_start")
    assert start["message"]["ai_marking"] == MARK


@pytest.mark.parametrize(("path", "body"), [OPENAI, ANTHROPIC])
async def test_marking_off_adds_no_body_field(path: str, body: dict[str, Any]) -> None:
    async with _client(_app("minimal")) as client:
        complete = await client.post(path, json=body)
        streamed = await client.post(path, json={**body, "stream": True})
    assert "ai_marking" not in complete.json()
    assert "ai_marking" not in streamed.text


async def test_the_marked_response_exposes_the_marking_headers_to_browsers() -> None:
    async with _client(_app()) as client:
        res = await client.post("/v1/run", json={"input": {"text": "hi"}})
    exposed = {h.strip().lower() for h in res.headers["access-control-expose-headers"].split(",")}
    assert {"x-ai-generated", "x-ai-disclosure-required"} <= exposed


@pytest.mark.parametrize(("risk", "flag"), [("limited", True), ("minimal", None)])
async def test_result_events_carry_the_aigenerated_extension(risk: str, flag: bool | None) -> None:
    events = InMemoryBus()
    app = _app(risk, events=events)
    async with _client(app) as client:
        await client.post("/v1/run", json={"input": {"text": "hi"}})
        await app.state.result_publisher.wait(5)
    published = [event for _, event in events.published]
    assert published
    assert published[0].aigenerated is flag
    wire = json.loads(published[0].to_wire())
    assert wire.get("aigenerated") is flag
