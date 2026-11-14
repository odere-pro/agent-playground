"""`StreamingASGITransport`, the in-memory transport of the `inprocess` lane (009 CH-1, P-7):
the body streams while the app runs, a closed response ends the app call, and an app failure is
reported, not lost. No socket."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from typing import Any

import anyio
import httpx
import pytest
from chassis.adapters.a2a import asgi_stream
from chassis.adapters.a2a.asgi_stream import StreamingASGITransport

WAIT_S = 2.0


class _Gated:
    """An app that sends one chunk, then waits for `go` before the last. `ended` is set when the
    call ends for any reason; `disconnected` when it read `http.disconnect`."""

    def __init__(self) -> None:
        self.go = asyncio.Event()
        self.ended = asyncio.Event()
        self.disconnected = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        try:
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            waiter = asyncio.ensure_future(self.go.wait())
            leaver = asyncio.ensure_future(receive())
            done, pending = await asyncio.wait({waiter, leaver}, return_when="FIRST_COMPLETED")
            for task in pending:
                task.cancel()
            if leaver in done and leaver.result()["type"] == "http.disconnect":
                self.disconnected.set()
                return
            await send({"type": "http.response.body", "body": b"last"})
        finally:
            self.ended.set()


async def test_the_first_chunk_arrives_before_the_app_ends() -> None:
    """The body streams: the first chunk is read while the app still waits."""
    app = _Gated()
    transport = StreamingASGITransport(app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://t") as c,
        c.stream("GET", "/") as response,
    ):
        chunks = response.aiter_bytes()
        async with asyncio.timeout(WAIT_S):
            assert await anext(chunks) == b"first"
        assert not app.ended.is_set()
        app.go.set()
        assert [chunk async for chunk in chunks] == [b"last"]


async def test_closing_the_response_disconnects_and_ends_the_app_call() -> None:
    """A response closed early sends `http.disconnect`, and the app call has ended on return."""
    app = _Gated()
    async with httpx.AsyncClient(transport=StreamingASGITransport(app), base_url="http://t") as c:
        async with c.stream("GET", "/") as response:
            assert await anext(response.aiter_bytes()) == b"first"
        assert app.disconnected.is_set() and app.ended.is_set()


async def test_closing_the_transport_ends_a_response_nobody_closed() -> None:
    """A response left open (an async generator dropped before its `finally`) ends when the
    client closes, as a socket pool drops its connections."""
    app = _Gated()
    client = httpx.AsyncClient(transport=StreamingASGITransport(app), base_url="http://t")
    response = await client.send(client.build_request("GET", "/"), stream=True)
    assert response.status_code == 200 and not app.ended.is_set()
    await client.aclose()
    assert app.ended.is_set()


async def test_an_app_error_before_the_headers_is_raised() -> None:
    """An exception before `http.response.start` comes out of the request, as in
    `httpx.ASGITransport`."""

    async def broken(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    async with httpx.AsyncClient(
        transport=StreamingASGITransport(broken), base_url="http://t"
    ) as c:
        with pytest.raises(RuntimeError, match="boom"):
            await c.get("/")


async def test_an_app_error_after_the_headers_is_a_read_error() -> None:
    """An exception mid-body is `httpx.ReadError`: the A2A client sees a transport failure."""

    async def broken(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"part", "more_body": True})
        raise RuntimeError("boom")

    async with (
        httpx.AsyncClient(transport=StreamingASGITransport(broken), base_url="http://t") as c,
        c.stream("GET", "/") as response,
    ):
        with pytest.raises(httpx.ReadError) as raised:
            await response.aread()
    assert str(raised.value) == asgi_stream.APP_FAILED_TEXT
    assert "boom" not in str(raised.value)
    assert isinstance(raised.value.__cause__, RuntimeError)


class _Deaf:
    """An app that sends its headers and one chunk, then never reads `http.disconnect` and
    never ends on its own. `cancelled` is set when its task is cancelled."""

    def __init__(self, *, headers: bool = True) -> None:
        self.headers = headers
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        try:
            await receive()
            if self.headers:
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"first", "more_body": True})
            self.entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def _read_all(response: httpx.Response) -> str:
    """Read the body to its end; the outcome as a word, so a reader that ends is visible."""
    try:
        async for _ in response.aiter_raw():
            pass
    except httpx.ReadError:
        return "read_error"
    return "ended"


async def test_an_app_that_ignores_disconnect_is_cancelled_and_a_reader_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed under a cancelled deadline scope, the app call is still cancelled and awaited,
    and a reader of the same body ends instead of hanging."""
    monkeypatch.setattr(asgi_stream, "CLOSE_GRACE_S", 0.05)
    app = _Deaf()
    async with httpx.AsyncClient(transport=StreamingASGITransport(app), base_url="http://t") as c:
        response = await c.send(c.build_request("GET", "/"), stream=True)
        await app.entered.wait()
        reader = asyncio.create_task(_read_all(response))
        await asyncio.sleep(0)
        with anyio.CancelScope() as deadline:
            deadline.cancel()
            await response.aclose()
        assert app.cancelled.is_set()
        async with asyncio.timeout(WAIT_S):
            assert await reader == "read_error"


async def test_closing_the_client_while_the_request_waits_for_headers_ends_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`aclose` while the app has sent no headers cancels the app call and the request ends
    with a transport error, never a hang."""
    monkeypatch.setattr(asgi_stream, "CLOSE_GRACE_S", 0.05)
    app = _Deaf(headers=False)
    client = httpx.AsyncClient(transport=StreamingASGITransport(app), base_url="http://t")
    request = asyncio.create_task(client.get("/"))
    await app.entered.wait()
    async with asyncio.timeout(WAIT_S):
        await client.aclose()
        with pytest.raises(httpx.ReadError):
            await request
    assert app.cancelled.is_set()


async def test_a_reader_that_falls_behind_holds_the_app_and_closing_still_ends_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body queue is bounded: an app with no reader blocks in `send`, and closing the
    response with the queue full still cancels and awaits the app call."""
    monkeypatch.setattr(asgi_stream, "CLOSE_GRACE_S", 0.05)
    monkeypatch.setattr(asgi_stream, "BODY_QUEUE_MAX", 4)
    sent = 0
    ended = asyncio.Event()

    async def flood(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        nonlocal sent
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            for _ in range(1000):
                await send({"type": "http.response.body", "body": b"x", "more_body": True})
                sent += 1
        finally:
            ended.set()

    async with httpx.AsyncClient(transport=StreamingASGITransport(flood), base_url="http://t") as c:
        response = await c.send(c.build_request("GET", "/"), stream=True)
        for _ in range(20):
            await asyncio.sleep(0)
        assert sent <= asgi_stream.BODY_QUEUE_MAX
        async with asyncio.timeout(WAIT_S):
            await response.aclose()
        assert ended.is_set()


async def test_an_app_that_sends_no_response_is_an_error() -> None:
    """An app that returns without `http.response.start` is a `RuntimeError`."""

    async def silent(scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        return None

    async with httpx.AsyncClient(
        transport=StreamingASGITransport(silent), base_url="http://t"
    ) as c:
        with pytest.raises(RuntimeError, match="sent no response"):
            await c.get("/")
