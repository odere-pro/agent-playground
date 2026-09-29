"""FakeEngine: emits a scripted event stream, or wraps any `handle` by a direct call.

It is a test double for the chassis's own unit tests, not a lane. The lanes (`inprocess`
over A2A in memory, `sidecar`, `remote`) come with 009 CH-1 and 055 CH-6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from chassis.core.envelope import Context, Request
from chassis.core.events import Event
from chassis.core.handle import Handle
from chassis.ports.engine import Lane

if TYPE_CHECKING:
    from chassis.ports.bundle import PortBundle


class FakeEngine:
    kind: Lane = "inprocess"
    capabilities: frozenset[str] = frozenset({"streaming"})

    def __init__(self, events: Sequence[Event] | None = None, handle: Handle | None = None) -> None:
        if (events is None) == (handle is None):
            raise ValueError("give either a scripted event list or a handle")
        self._events = list(events or [])
        self._handle = handle
        self.setup_config: Mapping[str, Any] | None = None
        self.closed = False
        self.runs = 0

    async def setup(self, config: Mapping[str, Any], ports: PortBundle) -> None:
        self.setup_config = config

    async def run(self, request: Request, ctx: Context) -> AsyncIterator[Event]:
        self.runs += 1
        if self._handle is not None:
            async for event in self._handle(request.input, ctx):
                yield event
            return
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True
