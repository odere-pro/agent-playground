"""The hold rule (PoC-3 open note, section 3): a streaming OpenAI or Anthropic answer sends nothing
until the first `delta` or the terminal event (`end` or `error`) arrives. If that event is an
`error`, the answer is an HTTP error, not SSE.

`hold(run.events())` reads the run's one event iterator up to and including that event and
returns a `Held`: the events read so far (`head`), the deciding event (`decider`), and `events()`,
which gives the head again and then the rest of the same iterator, so nothing is read twice and
nothing is lost. A router either streams `held.events()` or, on `held.error`, calls `aclose()`.
`serve` (`chassis.server.interfaces.serve`) does both.

The iterator is read in a task of its own. The peek runs in the request handler's task and the
rest in the response's, but `Run.events()` enters the `chassis.run` span when it starts and leaves
it when it ends: both must happen in one context (a context variable set in one task cannot be
reset in another, and OpenTelemetry fails to detach). The task reads one event per request, so
nothing past the event asked for is read, and a slow client still slows the run.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing

from chassis.core.events import Delta, End, Error, Event

__all__ = ["Held", "hold"]


class _Reader:
    """Reads `source` in its own task, one event per `next()`. `aclose()` cancels the task, which
    closes `source` inside that task.
    """

    def __init__(self, source: AsyncGenerator[Event]) -> None:
        self._source = source
        self._asks: asyncio.Queue[None] = asyncio.Queue()
        self._answers: asyncio.Queue[Event | BaseException | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            async with aclosing(self._source) as events:
                while True:
                    await self._asks.get()
                    try:
                        event = await anext(events)
                    except StopAsyncIteration:
                        self._answers.put_nowait(None)
                        return
                    self._answers.put_nowait(event)
        except Exception as exc:  # handed to the reader; the run's own errors are events
            self._answers.put_nowait(exc)

    async def next(self) -> Event | None:
        """The next event, or None once the source has ended or the task is gone."""
        if self._task.done() and self._answers.empty():
            return None
        self._asks.put_nowait(None)
        answer = asyncio.ensure_future(self._answers.get())
        try:
            await asyncio.wait({answer, self._task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not answer.done():
                answer.cancel()
        if not answer.done() or answer.cancelled():
            return None
        got = answer.result()
        if isinstance(got, BaseException):
            raise got
        return got

    async def aclose(self) -> None:
        """Stop reading and close the source in its task. Idempotent."""
        if not self._task.done():
            self._task.cancel()
        await asyncio.wait({self._task})


class Held:
    """A run's events with the head already read. `events()` once, then or instead `aclose()`."""

    def __init__(
        self, head: tuple[Event, ...], decider: Delta | End | Error | None, reader: _Reader
    ) -> None:
        self.head = head
        """Every event read, the decider last when there is one."""
        self.decider = decider
        """The first `delta`, `end`, or `error`; None when the stream ended without any."""
        self._reader = reader
        self._taken = False

    @property
    def error(self) -> Error | None:
        """The `error` that came before any text: answer it as HTTP."""
        return self.decider if isinstance(self.decider, Error) else None

    def events(self) -> AsyncGenerator[Event]:
        """The head, then the rest of the same iterator. Closes the run at the end; a router also
        calls `aclose()` when the response ends, since a generator never started never runs its
        `finally`.
        """
        if self._taken:
            raise RuntimeError("the held events can be taken once")
        self._taken = True
        return self._replay()

    async def _replay(self) -> AsyncGenerator[Event]:
        try:
            for seen in self.head:
                yield seen
            while (event := await self._reader.next()) is not None:
                yield event
        finally:
            await self._reader.aclose()

    async def aclose(self) -> None:
        """Close the run's events without reading on: its span ends and its trace id is freed.
        Idempotent.
        """
        await self._reader.aclose()


async def hold(events: AsyncGenerator[Event]) -> Held:
    """Read `events` up to and including the first `delta`, `end`, or `error`. Closes `events`
    when reading raises (cancellation included).
    """
    reader = _Reader(events)
    head: list[Event] = []
    try:
        while (event := await reader.next()) is not None:
            head.append(event)
            if isinstance(event, Delta | End | Error):
                return Held(tuple(head), event, reader)
    except BaseException:
        await reader.aclose()
        raise
    return Held(tuple(head), None, reader)
