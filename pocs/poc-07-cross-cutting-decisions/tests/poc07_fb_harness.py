"""Shared harness for the PoC-7 feedback scenarios. Not a test module.

A chassis app with `spec.adapters.feedback: langfuse` whose `LangfuseFeedback` talks to the fake
Langfuse API (`packages/fake-langfuse`) over an ASGI transport: no socket, no key. The engine is
injected: `FakeEngine` over `echo`, or a `SidecarConnector` to a workload on a Unix socket.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from chassis.adapters.langfuse import LangfuseFeedback
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.events import InMemoryBus
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.state import InMemoryState, StatePort
from chassis.server import ChassisConfig, create_app
from fake_langfuse import FakeLangfuse
from fake_langfuse import create_app as create_langfuse_app
from fastapi import FastAPI

PK, SK = "pk-lf-poc07", "sk-lf-poc07-not-a-secret"
AGENT = "poc07-feedback"
ECHO_ENGINE = {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}


def fake_langfuse() -> FakeLangfuse:
    return FakeLangfuse(PK, SK)


def langfuse_port(fake: FakeLangfuse) -> LangfuseFeedback:
    transport = httpx.ASGITransport(app=create_langfuse_app(fake))
    return LangfuseFeedback("http://langfuse", PK, SK, transport=transport)


def config(engine: dict[str, Any] | None = None, **feedback: Any) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc07",
            "profile": "fake",
            "agent": {"name": AGENT, "version": "0.0.1"},
            "spec": {
                "adapters": {"feedback": "langfuse"},
                "engine": engine or ECHO_ENGINE,
                "model": {"route": "big-default"},
                "feedback": feedback,
            },
        }
    )


def chassis(
    fake: FakeLangfuse,
    *,
    engine: EngineConnector | None = None,
    engine_spec: dict[str, Any] | None = None,
    state: StatePort | None = None,
    bus: InMemoryBus | None = None,
    **feedback: Any,
) -> FastAPI:
    ports = PortBundle(
        model=ScriptedModel(),
        engine=engine or FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=state or InMemoryState(),
        events=bus or InMemoryBus(),
        feedback=langfuse_port(fake),
    )
    return create_app(config(engine_spec, **feedback), ports)


@asynccontextmanager
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://chassis", timeout=30
        ) as http:
            yield http


async def run(http: httpx.AsyncClient, text: str = "simplify: Hello.") -> dict[str, Any]:
    answer = await http.post("/v1/run", json={"input": {"text": text}})
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body
