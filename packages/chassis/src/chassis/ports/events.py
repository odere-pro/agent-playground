"""EventPort: publish and subscribe CloudEvents 1.0 (structured mode, JSON), at least once.

No `cloudevents` SDK: `CloudEvent` below is the whole envelope. The promise, checked by
`chassis_contracts.events.EventPortContract`: `publish` returns once the broker has the event
and raises `PublishFailed` after the adapter's own retries; a handler that returns acknowledges
the event, one that raises gets it again, up to `max_attempts` deliveries, then the event goes to
`<topic>.dlq` with `deadletterreason` (the exception class name only) and `deadletterattempts`;
events with one `partitionkey` reach one group in publish order; each `group` gets every event.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

CONTENT_TYPE = "application/cloudevents+json"
"""The wire form's content type, the same bytes for every broker."""
DLQ_SUFFIX = ".dlq"
"""suggested: the dead-letter topic is `<topic>.dlq`."""


class CloudEvent(BaseModel):
    """CloudEvents 1.0, structured mode, JSON. Extensions are top-level attributes."""

    model_config = ConfigDict(extra="forbid")
    specversion: Literal["1.0"] = "1.0"
    id: str
    """`uuid4().hex`."""
    source: str
    """suggested: `/agents/<agent name>`."""
    type: str
    """The topic name, for example `agents.task.completed.v1`."""
    subject: str | None = None
    """suggested: the request_id."""
    time: datetime
    """UTC, RFC 3339."""
    datacontenttype: Literal["application/json"] = "application/json"
    dataschema: str | None = None
    """suggested: `task-result.v1.json`."""
    data: dict[str, Any]
    traceparent: str | None = None
    idempotencykey: str | None = None
    configversion: str | None = None
    modelroute: str | None = None
    partitionkey: str | None = None
    """The CloudEvents partitioning extension. suggested: the run's `idempotency_key`."""
    deadletterreason: str | None = None
    """Set on the dead-letter topic only: the handler's exception class name, never its text."""
    deadletterattempts: int | None = None
    """Set on the dead-letter topic only: how many deliveries failed."""

    def to_wire(self) -> bytes:
        """The structured JSON form every adapter sends."""
        return self.model_dump_json(exclude_none=True).encode()


class PublishFailed(RuntimeError):
    """The broker did not take the event after the adapter's own retries. No credential inside."""


EventHandler = Callable[[CloudEvent], Awaitable[None]]


class Subscription(Protocol):
    async def close(self) -> None: ...


class EventPort(Protocol):
    async def publish(self, topic: str, event: CloudEvent) -> None: ...

    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> Subscription: ...

    async def aclose(self) -> None: ...


class NoEvents:
    """No events adapter (`spec.adapters.events: none`). A publish is dropped; subscribing is an
    error, since nothing would ever arrive.
    """

    async def publish(self, topic: str, event: CloudEvent) -> None:
        return None

    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> Subscription:
        raise RuntimeError("no events adapter")

    async def aclose(self) -> None:
        return None
