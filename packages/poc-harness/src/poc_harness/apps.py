"""The chassis app with injected ports, its proxy app, their lifespans, and a client over ASGI.

Also the direct path: a workload's wire-form `handle` called with a canonical request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
from chassis.adapters.a2a import InProcessConnector
from chassis.adapters.a2a.inprocess import load_handle
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Context, Request
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.ports.bundle import PortBundle
from chassis.ports.engine import EngineConnector
from chassis.ports.model import ModelPort
from chassis.server import ChassisConfig, create_app
from chassis.server.proxy_app import create_proxy_app
from chassis_contracts.helpers import make_context, make_request
from fake_model_server import Script
from fake_model_server import create_app as create_fake_model_app

from poc_harness.paths import EXAMPLE_SCRIPT
from poc_harness.scenario import ROUTE


def fake_model_over_litellm() -> LiteLLMModel:
    """The chassis's real model adapter, pointed at the fake model server over ASGI."""
    app = create_fake_model_app(Script.from_yaml(EXAMPLE_SCRIPT))
    return LiteLLMModel("http://fake/v1", agent="poc-02", transport=httpx.ASGITransport(app=app))


def chassis_config(engine: Mapping[str, Any]) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc2",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "adapters": {"model": "fake"},
                "engine": dict(engine),
                "model": {"route": ROUTE},
                "prompt": {"version": "simplifier-v1"},
            },
        }
    )


def chassis_app(
    handle: str | None = None,
    *,
    model: ModelPort | None = None,
    tools: Any = None,
    connector: EngineConnector | None = None,
    engine: Mapping[str, Any] | None = None,
) -> Any:
    """The chassis app with injected ports. Default lane: `inprocess` serving `handle`; default
    model: LiteLLM over the fake model server. `tools` goes to `PortBundle.tools` when given.
    """
    spec = dict(engine or {"connector": "inprocess", "handle": handle})
    extra: dict[str, Any] = {} if tools is None else {"tools": tools}
    ports = PortBundle(
        model=model or fake_model_over_litellm(),
        engine=connector or InProcessConnector(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        **extra,
    )
    return create_app(chassis_config(spec), ports)


def proxy_for(app: Any) -> Any:
    """The localhost-only proxy app over `app`: what a workload's model and MCP calls reach. No
    fallback to the public app: the proxies moving back to the public port must fail a test. Call
    it once per app, before the app starts (it hooks the MCP lifespan into the public app's).
    """
    return create_proxy_app(app)


def bundle_for(connector: EngineConnector, *, model: ModelPort | None = None) -> PortBundle:
    """The ports a connector's `setup` takes. Default model: LiteLLM over the fake model server."""
    return PortBundle(
        model=model or fake_model_over_litellm(),
        engine=connector,
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )


@asynccontextmanager
async def running(*apps: Any) -> AsyncIterator[None]:
    """Run the lifespan of each distinct app, in order, in a task of its own, so a fixture can
    hold it across its `yield` (FastMCP's lifespan opens a task group, which must exit in the
    task it entered).
    """
    distinct = list({id(app): app for app in apps}.values())
    ready = asyncio.Event()
    stop = asyncio.Event()

    async def hold() -> None:
        async with AsyncExitStack() as stack:
            for app in distinct:
                await stack.enter_async_context(app.router.lifespan_context(app))
            ready.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(ready.wait())
    done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiter.cancel()
        task.result()
        raise RuntimeError("the lifespan ended before it was ready")
    try:
        yield
    finally:
        stop.set()
        await task


def client_for(app: Any, base_url: str = "http://chassis") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url, follow_redirects=True
    )


def request_and_context(text: str, request_id: str = "req-poc2") -> tuple[Request, Context]:
    request = make_request(text=text, request_id=request_id)
    return request, make_context(request, model_route=ROUTE)


async def run_handle(path: str, text: str) -> list[dict[str, Any]]:
    """Call a workload's wire-form `handle` directly and collect its events as dicts."""
    request, ctx = request_and_context(text)
    handle = load_handle(path)
    out: list[dict[str, Any]] = []
    async for raw in handle(request.input.model_dump(mode="json"), ctx.model_dump(mode="json")):
        dump = getattr(raw, "model_dump", None)
        out.append(dump(mode="json") if callable(dump) else dict(raw))
    return out
