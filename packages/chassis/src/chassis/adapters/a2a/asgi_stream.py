"""`StreamingASGITransport`: an httpx transport that calls an ASGI app in memory, like
`httpx.ASGITransport`, but returns the response as soon as the app sends its headers and streams
the body as the app sends it. No socket.

`httpx.ASGITransport` awaits the whole app call before it returns, so an SSE stream arrives in one
batch after the app ends. The `inprocess` lane needs the stream as it goes: the connector reads
the task id from the first A2A item, and only with it can a timeout or a caller that leaves send
`CancelTask` (009 CH-1, proposals.md P-7). That cancel is the one every lane uses.

The app runs in its own task. Closing the response tells the app the client left
(`http.disconnect`), then cancels its task after `CLOSE_GRACE_S` and waits for it, so no app call
outlives its response. Closing the transport (the httpx client's `aclose`) does the same for
every call still open. An exception from the app before its headers is raised from
`handle_async_request`, as `httpx.ASGITransport` does; after the headers, it is raised from the
body stream, as `httpx.ReadError`, so the A2A client sees a transport failure, not a chassis
bug.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import Any

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


class _Done:
    """The end of the body: the app sent its last chunk, or raised (`error`)."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error


class _Call:
    """One app call: its task, and `left`, set when the client is gone (`http.disconnect`)."""

    def __init__(self, task: asyncio.Task[None], left: asyncio.Event) -> None:
        self.task = task
        self.left = left

    async def stop(self) -> None:
        """Tell the app the client left, give it `CLOSE_GRACE_S` to end, then cancel it and wait.
        The call never raises (a failure goes to the reader); a cancel of the caller still
        propagates."""
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
                    raise httpx.ReadError(f"the in-process app failed: {chunk.error!r}")
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


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """See the module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._calls: set[_Call] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        chunks: asyncio.Queue[bytes | _Done] = asyncio.Queue()
        started: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        left = asyncio.Event()
        sent_body = False

        async def receive() -> Message:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await left.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                if not started.done():
                    started.set_result(message)
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if chunk and request.method != "HEAD":
                    chunks.put_nowait(chunk)
                if not message.get("more_body", False):
                    chunks.put_nowait(_Done())

        async def call() -> None:
            try:
                await self.app(_scope(request), receive, send)
            except Exception as exc:  # reported to the reader, before or after the headers
                if not started.done():
                    started.set_exception(exc)
                chunks.put_nowait(_Done(exc))
                return
            if not started.done():
                started.set_exception(RuntimeError("the in-process app sent no response"))
            chunks.put_nowait(_Done())

        task = asyncio.create_task(call(), name=f"inprocess {request.method} {request.url.path}")
        running = _Call(task, left)
        self._calls.add(running)
        task.add_done_callback(lambda _: self._calls.discard(running))
        try:
            start = await started
        except BaseException:
            await running.stop()
            raise
        return httpx.Response(
            start["status"], headers=start.get("headers", []), stream=_Body(chunks, running)
        )

    async def aclose(self) -> None:
        """Stop every app call still open, as closing a socket pool drops its connections. A
        response nobody closed (an async generator dropped before its `finally`) ends here."""
        for running in list(self._calls):
            await running.stop()
