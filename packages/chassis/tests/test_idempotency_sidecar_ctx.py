"""018 H-18: the idempotency key reaches the workload in `ctx` in the `sidecar` lane.

The public chassis app and the template A2A server (`workload_a2a`) each run on uvicorn over a
Unix socket, so the run crosses the real A2A hop. The workload streams back the
`idempotency_key` it reads from `ctx`. No TCP, no key.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from a2a_uds import SIDECAR_URL, serve_uds
from chassis.adapters.a2a import SidecarConnector
from chassis.fakes import InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.fakes.state import InMemoryState
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app

HANG_GUARD_S = 30.0
CLIENT_KEY = "client-key-018"  # pragma: allowlist secret (an idempotency key, not a credential)


async def ctx_echo(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """An echo workload in the wire form that returns what it got in `ctx.idempotency_key`."""
    yield {"type": "start", "request_id": ctx["request_id"]}
    yield {"type": "delta", "text": str(ctx.get("idempotency_key"))}
    yield {"type": "end", "status": "ok"}


def _workload_app() -> Any:
    from workload_a2a.server import build_agent_card, build_app

    return build_app(ctx_echo, build_agent_card(name="ctx-echo", version="1", url=SIDECAR_URL))


def _config(uds: str) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-1",
            "profile": "local",
            "agent": {"name": "echo", "version": "0.0.1"},
            "spec": {
                "adapters": {"config": "memory"},
                "engine": {"connector": "sidecar", "url": SIDECAR_URL, "uds": uds},
                "model": {"route": "fake-route"},
            },
        }
    )


def _ports() -> PortBundle:
    return PortBundle(
        model=ScriptedModel(),
        engine=SidecarConnector(),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        state=InMemoryState(),
    )


@asynccontextmanager
async def _chassis(workload_uds: str) -> AsyncIterator[httpx.AsyncClient]:
    """The public chassis app, lifespan on, over a fresh Unix socket; yields a client for it."""
    app = create_app(_config(workload_uds), _ports())
    with tempfile.TemporaryDirectory(prefix="h18-") as folder:  # short: macOS caps the path
        path = str(Path(folder, "public.sock"))
        server = uvicorn.Server(uvicorn.Config(app, uds=path, lifespan="on", log_level="warning"))
        task = asyncio.create_task(server.serve())
        try:
            async with asyncio.timeout(HANG_GUARD_S):
                while not server.started:
                    if task.done():
                        task.result()
                        raise RuntimeError("uvicorn exited before it started")
                    await asyncio.sleep(0.01)
            transport = httpx.AsyncHTTPTransport(uds=path)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://chassis", timeout=10
            ) as client:
                yield client
        finally:
            server.should_exit = True
            server.force_exit = True
            await task


async def test_the_openai_idempotency_key_header_reaches_the_workload_ctx_in_sidecar() -> None:
    body = {"model": "echo", "messages": [{"role": "user", "content": "hi"}]}
    async with serve_uds(_workload_app()) as uds, _chassis(uds) as client:
        response = await client.post(
            "/v1/chat/completions", json=body, headers={"Idempotency-Key": CLIENT_KEY}
        )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == CLIENT_KEY


async def test_the_native_envelope_key_reaches_the_workload_ctx_in_sidecar() -> None:
    body = {"input": {"text": "hi"}, "idempotency_key": CLIENT_KEY}
    async with serve_uds(_workload_app()) as uds, _chassis(uds) as client:
        response = await client.post("/v1/run", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["idempotency_key"] == CLIENT_KEY
    assert payload["output"]["text"] == CLIENT_KEY


async def test_a_minted_key_reaches_the_workload_ctx_and_the_envelope_in_sidecar() -> None:
    async with serve_uds(_workload_app()) as uds, _chassis(uds) as client:
        response = await client.post("/v1/run", json={"input": {"text": "hi"}})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["idempotency_key"]
    assert payload["output"]["text"] == payload["idempotency_key"]
