"""Graceful shutdown: the chassis handles SIGTERM itself, not uvicorn (PoC-4 plan, section 6).

`chassis serve` runs two `uvicorn.Server`s in one loop. Each `serve()` would install its own
signal handlers, the second would win, and on exit uvicorn raises the captured signal again. So
both are `QuietServer`s, whose `capture_signals` does nothing, and `serve_pair` installs one
handler with `loop.add_signal_handler` for SIGTERM and SIGINT. The handler is a plain closure: it
never calls `Server.handle_exit` (sse-starlette patches that, and cuts every open stream when it
runs) and is never a bound method of a server (sse-starlette looks for one through
`signal.getsignal`).

On the first signal, `Drain.run` does, in order:

1. `state.draining = True`: `/ready` answers 503 `draining` at once. New calls are still served,
   but every public response now carries `Connection: close` (`CloseWhenDraining`), so each
   HTTP/1.1 connection ends after its response and the client reconnects through the load
   balancer, to another replica once this one is out of the endpoints.
2. The `on_drain` hooks run (the event consumer stops fetching, when there is one).
3. Sleep `delay_s`, the time a load balancer or Kubernetes endpoints take to stop sending.
4. `public.should_exit = True`. uvicorn closes the public listener and waits for in-flight
   requests, streams included, up to `timeout_s` (`timeout_graceful_shutdown`), then cancels the
   rest.
5. The public lifespan's shutdown runs and closes the ports.
6. Only then `proxy.should_exit = True`, and the same for the remote proxy listener when there is
   one (PoC-5, the `remote` lane): in-flight workloads call the model proxy and the tool endpoint
   until their runs end.

A second signal sets `force_exit` on every server. If any server stops on its own (a failed
startup), the others stop too.

Bind order (PoC-5 plan, section 2.12, H14). `Drain.serve` starts the proxy listeners first and
waits until each one has bound. Only then does it start the public server, whose lifespan builds
the ports and reaches the workload. A proxy that cannot bind raises `ProxyBindFailed`: nothing
else starts, and `chassis serve` exits non-zero. So a workload can never bind the proxy port
first, and the chassis never runs without its proxy.

`DeferredStartup` moves the wait for the workload to readiness. It wraps the public app's lifespan
so that uvicorn binds the public port at once (`/health` 200, `/ready` 503 `starting`) while the
real lifespan is entered in the background. An attempt that fails because a dependency is not
reachable yet (an `OSError` or an httpx transport error anywhere in the exception chain: the
sidecar's card fetch, most often) is retried every `interval_s` for up to `timeout_s` (suggested
120 s, the old Compose card-wait). Any other failure, or the end of the wait, stops the server and
`chassis serve` exits 3.

`CloseWhenDraining` is a pure ASGI middleware on the public app only (not `BaseHTTPMiddleware`:
streams and SSE pass through unbuffered). It looks at `state.draining` when a response starts and
then adds `Connection: close`. uvicorn (h11, and httptools too) honors an app-set
`Connection: close`: the response is sent in full, a stream to its `end` event, then the
connection closes. A response whose headers went out before the drain keeps its connection until
it ends; uvicorn closes it at step 4. The proxy listener never gets the header.

The limit, stated plainly: a keep-alive connection that stays idle through the whole drain delay
never sees `Connection: close`. At step 4 uvicorn closes it. A request the client writes on it at
that instant gets no answer ("Server disconnected without sending a response"). That is correct
HTTP: a client must retry an idempotent request on a closed idle connection, but httpx does not
retry a `POST`. No timeout here removes that race. So `--drain-delay-s` must exceed the load
balancer's endpoint-removal time plus the clients' idle reuse window (their keep-alive expiry),
so that every connection either carries a request (and gets `Connection: close`) or is dropped
by the client before step 4. Clients that cannot afford one lost `POST` retry on a connect error.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("chassis.lifecycle")

SIGNALS = (signal.SIGTERM, signal.SIGINT)


@dataclass(frozen=True)
class DrainSettings:
    delay_s: float = 5.0
    """suggested: how long `/ready` is 503 before the public listener closes."""
    timeout_s: float = 30.0
    """suggested: the default `budget.timeout_ms`; how long in-flight requests may run on."""


CLOSE = (b"connection", b"close")


class CloseWhenDraining:
    """Pure ASGI middleware: while `state.draining` is true, every HTTP response carries
    `Connection: close` (any other `Connection` value is replaced). Nothing is buffered.
    """

    def __init__(self, app: ASGIApp, state: Any) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_closing(message: Message) -> None:
            if message["type"] == "http.response.start" and getattr(self.state, "draining", False):
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"connection"
                ]
                message = {**message, "headers": [*headers, CLOSE]}
            await send(message)

        await self.app(scope, receive, send_closing)


class QuietServer(uvicorn.Server):
    """A uvicorn server that leaves signals alone; `serve_pair` owns them."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class ProxyBindFailed(Exception):
    """A proxy listener could not bind. Nothing else was started."""

    def __init__(self, name: str, server: uvicorn.Server) -> None:
        config = server.config
        self.where = config.uds or f"{config.host}:{config.port}"
        super().__init__(
            f"the {name} listener could not bind {self.where}: the address is in use or is not "
            "on this host. The chassis does not run without its proxy, so nothing else started."
        )


@dataclass
class Drain:
    """The start and shutdown order for one public server, one proxy server, and the optional
    remote proxy server. `order` records what stopped, for tests and the log.
    """

    public: uvicorn.Server
    proxy: uvicorn.Server
    settings: DrainSettings
    state: Any = None
    """The public app's `state`; `draining` is set on it."""
    on_drain: Sequence[Callable[[], Awaitable[None]]] = ()
    remote: uvicorn.Server | None = None
    """The remote proxy listener (PoC-5, the `remote` lane); it stops with the proxy."""
    signals: int = 0
    order: list[str] = field(default_factory=list)
    task: asyncio.Task[None] | None = None

    def _servers(self) -> list[uvicorn.Server]:
        return [s for s in (self.public, self.proxy, self.remote) if s is not None]

    def signal(self) -> None:
        """Called from the loop's signal handler. The first call starts the drain; the second
        forces every server out.
        """
        self.signals += 1
        if self.signals == 1:
            log.info("signal received: draining")
            self.task = asyncio.get_running_loop().create_task(self.run(), name="chassis.drain")
            return
        log.warning("second signal: exiting now")
        for server in self._servers():
            server.force_exit = True
            server.should_exit = True

    async def run(self) -> None:
        if self.state is not None:
            self.state.draining = True
        self.order.append("draining")
        for hook in self.on_drain:
            try:
                await hook()
            except Exception:
                log.exception("a drain hook failed")
        await asyncio.sleep(self.settings.delay_s)
        self.order.append("public closing")
        self.public.should_exit = True

    async def _run(self, name: str, server: uvicorn.Server) -> None:
        try:
            await server.serve()
        except SystemExit:  # uvicorn exits on a failed bind or lifespan; `started` tells
            log.error("the %s listener failed to start", name)
        finally:
            self.order.append(f"{name} stopped")
            for other in self._servers():  # step 6: the proxies go after the public server
                if other is not server:
                    other.should_exit = True

    async def _bind_proxies(
        self, proxies: list[tuple[str, uvicorn.Server]]
    ) -> list[asyncio.Task[None]]:
        """Start the proxy listeners and wait until each has bound. Raises `ProxyBindFailed`
        after stopping the others when one cannot.
        """
        tasks = [asyncio.create_task(self._run(name, server)) for name, server in proxies]
        while not all(server.started for _, server in proxies):
            failed = next(
                (
                    (name, server)
                    for (name, server), task in zip(proxies, tasks, strict=True)
                    if task.done() and not server.started
                ),
                None,
            )
            if failed is not None:
                for _, server in proxies:
                    server.should_exit = True
                await asyncio.gather(*tasks, return_exceptions=True)
                raise ProxyBindFailed(*failed)
            await asyncio.sleep(0.01)
        return tasks

    async def serve(self) -> None:
        """Every server in this loop, with the signal handlers installed: the proxies bind
        first, then the public server starts. Returns when all have stopped.
        """
        proxies = [("proxy", self.proxy)]
        if self.remote is not None:
            proxies.append(("remote proxy", self.remote))
        with handle_signals(self.signal):
            try:
                tasks = await self._bind_proxies(proxies)
                tasks.append(asyncio.create_task(self._run("public", self.public)))
                await asyncio.gather(*tasks)
            finally:
                if self.task is not None and not self.task.done():
                    self.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.task


@dataclass(frozen=True)
class StartupWait:
    """How long the public lifespan may wait for a dependency that is not reachable yet."""

    timeout_s: float = 120.0
    """suggested: the Compose card-wait this replaces (H14)."""
    interval_s: float = 1.0
    """suggested: the pause between attempts."""


def _unreachable(exc: BaseException) -> bool:
    """True when the exception chain holds a connection failure: worth another attempt."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, (OSError, httpx.TransportError)):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


class DeferredStartup:
    """Enter `app`'s lifespan in the background, retrying while a dependency is not reachable,
    so the public listener binds at once and `/ready` says `starting` meanwhile. `install()`
    replaces `app.router.lifespan_context`; call it after every hook into that lifespan (the
    tool endpoints). Set `server` so a failure stops it; `failed` holds the error.
    """

    def __init__(self, app: Any, wait: StartupWait) -> None:
        self.app = app
        self.wait = wait
        self.server: uvicorn.Server | None = None
        self.started = False
        self.failed: BaseException | None = None
        self._inner: Callable[[Any], AbstractAsyncContextManager[Any]] = app.router.lifespan_context
        self._stop = asyncio.Event()

    def install(self) -> None:
        self._inner = self.app.router.lifespan_context

        @contextlib.asynccontextmanager
        async def lifespan(app: Any) -> AsyncIterator[None]:
            self._stop = asyncio.Event()
            task = asyncio.create_task(self._own(app), name="chassis.startup")
            try:
                yield
            finally:
                self._stop.set()
                if not self.started and not task.done():
                    task.cancel()  # still waiting for a dependency: nothing to exit
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self.app.router.lifespan_context = lifespan

    async def _own(self, app: Any) -> None:
        """Enter the inner lifespan, hold it until shutdown, and exit it, all in this one task.
        Spans' `ContextVar` tokens and anyio cancel scopes (FastMCP's session managers) must be
        exited by the task that entered them."""
        manager = await self._enter(app)
        if manager is None:
            return
        try:
            await self._stop.wait()
        finally:
            await manager.__aexit__(None, None, None)

    async def _enter(self, app: Any) -> AbstractAsyncContextManager[Any] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wait.timeout_s
        attempts = 0
        while True:
            attempts += 1
            manager = self._inner(app)
            try:
                await manager.__aenter__()
            except Exception as exc:
                if _unreachable(exc) and loop.time() + self.wait.interval_s < deadline:
                    level = logging.WARNING if attempts == 1 else logging.INFO
                    log.log(
                        level,
                        "startup: a dependency is not reachable yet (attempt %d): %s",
                        attempts,
                        exc,
                    )
                    await asyncio.sleep(self.wait.interval_s)
                    continue
                log.error("startup failed after %d attempt(s): %s", attempts, exc)
                self.failed = exc
                if self.server is not None:
                    self.server.should_exit = True
                return None
            self.started = True
            log.info("startup done after %d attempt(s)", attempts)
            return manager


@contextlib.contextmanager
def handle_signals(on_signal: Callable[[], None]) -> Iterator[None]:
    """Route SIGTERM and SIGINT to `on_signal` in the running loop; restore the old handlers
    after.
    """
    loop = asyncio.get_running_loop()
    previous = {sig: signal.getsignal(sig) for sig in SIGNALS}

    def handler() -> None:  # a plain function, not a server's bound method
        on_signal()

    for sig in SIGNALS:
        loop.add_signal_handler(sig, handler)
    try:
        yield
    finally:
        for sig in SIGNALS:
            loop.remove_signal_handler(sig)
            signal.signal(sig, previous[sig])
