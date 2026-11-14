"""`StreamingASGITransport`: an httpx transport that calls an ASGI app in memory, like
`httpx.ASGITransport`, but returns the response as soon as the app sends its headers and streams
the body as the app sends it. No socket.

`httpx.ASGITransport` awaits the whole app call before it returns, so an SSE stream arrives in one
batch after the app ends. The `inprocess` lane needs the stream as it goes: the connector reads
the task id from the first A2A item, and only with it can a timeout or a caller that leaves send
`CancelTask` (009 CH-1, proposals.md P-7). That cancel is the one every lane uses.

The app runs in its own task. Closing the response tells the app the client left
(`http.disconnect`), then cancels its task after `CLOSE_GRACE_S` and waits for it, so no app call
outlives its response. The close is shielded, so it completes under a cancelled deadline scope
too. Closing the transport (the httpx client's `aclose`) does the same for every call still open.

The body queue holds at most `BODY_QUEUE_MAX` chunks: an app whose reader falls behind waits in
`send`. The end of the body is always queued, even when the queue is full or the app task is
cancelled, so a reader never hangs.

An exception from the app before its headers is raised from `handle_async_request` as it is, as
`httpx.ASGITransport` does. The pipeline reports it as `engine_error`, not as an A2A code. After
the headers, it is raised from the body stream as `httpx.ReadError` with the fixed text
`APP_FAILED_TEXT`, so the A2A client sees a transport failure; the app's words stay out of it, and
only the exception type is logged. A call that ends before its headers for any other reason (a
cancel, or no response) is `httpx.ReadError` or `RuntimeError` from the request.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Any

import anyio
import httpx

Message = MutableMapping[str, Any]
ASGIApp = Callable[
    [
        MutableMapping[str, Any],
        Callable[[], Awaitable[Message]],
        Callable[[Message], Awaitable[None]],
    ],
    Awaitable[None],
]
CLIENT = ("127.0.0.1", 123)
"""The `client` address in the ASGI scope, the value `httpx.ASGITransport` uses."""
CLOSE_GRACE_S = 1.0
"""suggested: how long a closed response waits for the app to end on `http.disconnect` before
its task is cancelled."""
BODY_QUEUE_MAX = 64
"""suggested: the most body chunks held for a reader that falls behind; past it `send` waits."""
APP_FAILED_TEXT = "the in-process app failed"
"""The fixed text of the `httpx.ReadError` for an app call that failed after its headers."""
CANCELLED_TEXT = "the in-process app call ended before its response"
"""The fixed text of the `httpx.ReadError` for an app call cancelled before its headers."""

logger = logging.getLogger(__name__)


class _Done:
    """The end of the body: the app sent its last chunk, or raised (`error`)."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error


def _end(chunks: asyncio.Queue[bytes | _Done], done: _Done) -> None:
    """Queue the end of the body without waiting. A full queue drops its oldest chunks: the call
    failed or the client left, so the reader gets the end, not the rest of the body."""
    while chunks.full():
        chunks.get_nowait()
    chunks.put_nowait(done)


class _Call:
    """One app call: its task, and `left`, set when the client is gone (`http.disconnect`)."""

    def __init__(self, task: asyncio.Task[None], left: asyncio.Event) -> None:
        self.task = task
        self.left = left

    async def stop(self) -> None:
        """Tell the app the client left, give it `CLOSE_GRACE_S` to end, then cancel it and wait.
        Shielded, like `A2AConnector._finish`: under a cancelled scope an unshielded await would
        be cancelled again at each checkpoint, and the app task would never be cancelled or
        awaited. The call never raises (a failure goes to the reader)."""
        with anyio.CancelScope(shield=True):
            self.left.set()
            await asyncio.wait({self.task}, timeout=CLOSE_GRACE_S)
            if not self.task.done():
                self.task.cancel()
                await asyncio.wait({self.task})


class _Body(httpx.AsyncByteStream):
    """The response body, chunk by chunk as the app sends it. `aclose` ends the app call."""

    def __init__(self, chunks: asyncio.Queue[bytes | _Done], call: _Call) -> None:
        self._chunks = chunks
        self._call = call

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._chunks.get()
            if isinstance(chunk, _Done):
                if chunk.error is not None:
                    logger.warning(
                        "in-process app failed after its headers",
                        extra={"error_type": type(chunk.error).__name__},
                    )
                    raise httpx.ReadError(APP_FAILED_TEXT) from chunk.error
                return
            yield chunk

    async def aclose(self) -> None:
        await self._call.stop()


def _scope(request: httpx.Request) -> dict[str, Any]:
    """The ASGI HTTP scope for `request`, built the way `httpx.ASGITransport` builds it."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": request.method,
        "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
        "scheme": request.url.scheme,
        "path": request.url.path,
        "raw_path": request.url.raw_path.split(b"?")[0],
        "query_string": request.url.query,
        "server": (request.url.host, request.url.port),
        "client": CLIENT,
        "root_path": "",
    }


def _fail_start(started: asyncio.Future[Message], error: BaseException | None) -> None:
    """Resolve `started` when the call ended with no headers, so the request never hangs."""
    if started.done():
        return
    if isinstance(error, Exception):
        started.set_exception(error)
    elif error is not None:
        started.set_exception(httpx.ReadError(CANCELLED_TEXT))
    else:
        started.set_exception(RuntimeError("the in-process app sent no response"))


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """See the module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._calls: set[_Call] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        chunks: asyncio.Queue[bytes | _Done] = asyncio.Queue(maxsize=BODY_QUEUE_MAX)
        started: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        left = asyncio.Event()
        sent_body = False
        ended = False

        async def receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await left.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            nonlocal ended
            if message["type"] == "http.response.start":
                if not started.done():
                    started.set_result(message)
            elif message["type"] == "http.response.body" and not ended:
                chunk = message.get("body", b"")
                if chunk and request.method != "HEAD":
                    await chunks.put(chunk)
                if not message.get("more_body", False):
                    await chunks.put(_Done())
                    ended = True

        async def call() -> None:
            error: BaseException | None = None
            try:
                await self.app(_scope(request), receive, send)
            except BaseException as exc:  # reported to the reader; a cancel is re-raised
                error = exc
                if not isinstance(exc, Exception):
                    raise
            finally:
                _fail_start(started, error)
                if not ended:
                    _end(chunks, _Done(error))

        task = asyncio.create_task(call(), name=f"inprocess {request.method} {request.url.path}")
        running = _Call(task, left)
        self._calls.add(running)
        task.add_done_callback(lambda _: self._calls.discard(running))
        try:
            start = await started
        except BaseException:
            await running.stop()
            if started.done() and not started.cancelled():
                started.exception()  # retrieved, so a stopped call logs no stray warning
            raise
        return httpx.Response(
            start["status"], headers=start.get("headers", []), stream=_Body(chunks, running)
        )

    async def aclose(self) -> None:
        """Stop every app call still open, as closing a socket pool drops its connections. A
        response nobody closed (an async generator dropped before its `finally`) ends here."""
        for running in list(self._calls):
            await running.stop()
