"""`DeferredStartup` enters and exits the wrapped lifespan in one task.

The public lifespan holds things that belong to the task that entered them: a span's
`ContextVar` token (`InMemoryTelemetry`, the OTel adapter) and anyio cancel scopes (FastMCP's
streamable-HTTP session managers for `/v1/mcp` and `/mcp`). Exiting them from another task
raises, so shutdown would skip the rest of the lifespan: the ports and the result publisher would
never close. These tests run the deferred lifespan the way uvicorn does (enter, serve, exit) and
check that the inner exit ran cleanly.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from chassis.fakes import InMemoryTelemetry
from chassis.server.lifecycle import DeferredStartup, StartupWait

_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("lifespan_var", default=None)


class _Router:
    def __init__(self, lifespan: Any) -> None:
        self.lifespan_context = lifespan


class _App:
    def __init__(self, lifespan: Any) -> None:
        self.router = _Router(lifespan)


async def _run(app: _App, deferred: DeferredStartup) -> None:
    async with app.router.lifespan_context(app):
        for _ in range(500):
            if deferred.started or deferred.failed is not None:
                break
            await asyncio.sleep(0.01)
        assert deferred.started, deferred.failed


async def test_a_context_var_token_set_at_startup_is_reset_at_shutdown() -> None:
    exited: list[str] = []

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        token = _var.set("started")
        try:
            yield
        finally:
            _var.reset(token)  # raises ValueError when run in another task's context
            exited.append("ok")

    app = _App(lifespan)
    deferred = DeferredStartup(app, StartupWait(timeout_s=5, interval_s=0.01))
    deferred.install()
    await _run(app, deferred)
    assert exited == ["ok"]


async def test_a_span_open_across_the_lifespan_ends_cleanly() -> None:
    telemetry = InMemoryTelemetry()

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        with telemetry.span("chassis.lifespan"):
            yield

    app = _App(lifespan)
    deferred = DeferredStartup(app, StartupWait(timeout_s=5, interval_s=0.01))
    deferred.install()
    await _run(app, deferred)
    assert [s.ended for s in telemetry.spans] == [True]


async def test_an_anyio_task_group_entered_at_startup_exits_at_shutdown() -> None:
    """FastMCP's session manager holds a task group for the whole lifespan."""
    exited: list[str] = []

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        async with anyio.create_task_group() as group:
            group.start_soon(anyio.sleep_forever)
            try:
                yield
            finally:
                group.cancel_scope.cancel()
        exited.append("ok")

    app = _App(lifespan)
    deferred = DeferredStartup(app, StartupWait(timeout_s=5, interval_s=0.01))
    deferred.install()
    await _run(app, deferred)
    assert exited == ["ok"]


async def test_shutdown_before_startup_finished_does_not_hang() -> None:
    entered = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        entered.set()
        await asyncio.sleep(3600)
        yield

    app = _App(lifespan)
    deferred = DeferredStartup(app, StartupWait(timeout_s=5, interval_s=0.01))
    deferred.install()
    async with asyncio.timeout(5):
        async with app.router.lifespan_context(app):
            await entered.wait()
    assert not deferred.started
