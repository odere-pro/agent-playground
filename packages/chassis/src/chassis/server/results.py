"""Result events (PoC-4 plan, section 2, use 1; 019 H-17): one CloudEvent after each run.

With `spec.events.result_events: true` the lifespan adds a `ResultPublisher` to
`RunPipeline.on_finished`. After a run whose engine stream ended (`Run.done`), it builds a
`TaskResult` from the run's response and wraps it in a `CloudEvent` of type
`agents.task.completed.v1` (status `ok`, `retry`, `fallback`) or `agents.task.failed.v1` (status
`error`). The partition key is the hash of the run's idempotency key, so one key's events keep
their order.

**No raw key.** The event never holds the client's idempotency key: anyone who reads the topic
could replay or collide with it. `TaskResult.idempotency_key` and the `idempotencykey` and
`partitionkey` extensions hold `key_hash(key)`, the sha256 hex of the key (the same hash
`server.idempotency.store_key` uses, without the agent prefix), so a consumer can still correlate
and partition. The field keeps its name; its value is the hash.

**A fixed `id` per run.** `id` is `event_id(request_id, type)`: a uuid5 (hex) of the request id
and the event type under `EVENT_ID_NAMESPACE`, so a consumer dedupes a second delivery of one
result by `id` (CloudEvents: `source` + `id` is unique per event).

The publish runs in a background task: a slow broker never delays an answer, and nothing it does
reaches the client. A publish that the broker took is counted as `chassis.events.published{topic}`;
a `PublishFailed` (or any other exception from the adapter) as
`chassis.events.publish_failed{topic}` and one warning log with the ids and the exception class,
never the payload (suggested names). A run that did not finish (the client left, the stream ended
early) publishes nothing, and neither does a replay: `serve` never calls `on_finished` for one.

At shutdown the lifespan awaits `aclose()` before the ports close: it waits for pending publishes
up to `SHUTDOWN_WAIT_S` (suggested: 5 s), then cancels the rest and counts them as
`chassis.events.publish_abandoned`.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime

from starlette.datastructures import State

from chassis.core.results import TASK_COMPLETED, TASK_FAILED, TaskResult
from chassis.core.trace import traceparent_for
from chassis.ports.bundle import PortBundle
from chassis.ports.events import CloudEvent, PublishFailed
from chassis.server.pipeline import Run

__all__ = [
    "DATASCHEMA",
    "EVENT_ID_NAMESPACE",
    "PUBLISHED",
    "PUBLISH_ABANDONED",
    "PUBLISH_FAILED",
    "SHUTDOWN_WAIT_S",
    "ResultPublisher",
    "event_id",
    "key_hash",
    "result_event",
]

PUBLISHED = "chassis.events.published"
"""suggested: a result event the broker took, labeled `topic`."""
PUBLISH_FAILED = "chassis.events.publish_failed"
"""suggested: a result event the broker refused after the adapter's retries, labeled `topic`."""
PUBLISH_ABANDONED = "chassis.events.publish_abandoned"
"""suggested: a result event still pending when the shutdown wait ran out."""
SHUTDOWN_WAIT_S = 5.0
"""suggested: how long shutdown waits for pending publishes."""
EVENT_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:chassis:agents.task")
"""suggested: the uuid5 namespace of result event ids."""


def event_id(request_id: str, event_type: str) -> str:
    """The CloudEvent `id` of a run's result: the same for the same run and type."""
    return uuid.uuid5(EVENT_ID_NAMESPACE, f"{event_type}:{request_id}").hex


def key_hash(idempotency_key: str) -> str:
    """The sha256 hex of an idempotency key: what a result event carries instead of the key."""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


DATASCHEMA = "task-result.v1.json"
"""suggested: the `dataschema` of a result event (`schemas/task-result.v1.json`)."""


async def result_event(run: Run) -> tuple[str, CloudEvent]:
    """The topic and the CloudEvent for a finished run. The run must be `done`."""
    response = await run.response()
    request = run.request
    topic = TASK_FAILED if response.status == "error" else TASK_COMPLETED
    hashed = key_hash(request.idempotency_key)
    result = TaskResult(
        agent=request.agent,
        agent_version=request.agent_version,
        request_id=request.request_id,
        idempotency_key=hashed,
        status=response.status,
        input=request.input,
        output=response.output,
        usage=response.metrics,
        versions=response.versions,
    )
    event = CloudEvent(
        id=event_id(request.request_id, topic),
        source=f"/agents/{request.agent}",
        type=topic,
        subject=request.request_id,
        time=datetime.now(UTC),
        dataschema=DATASCHEMA,
        data=result.model_dump(mode="json"),
        traceparent=run.ctx.traceparent or traceparent_for(run.ctx),
        idempotencykey=hashed,
        configversion=response.versions.config,
        modelroute=response.versions.model_route,
        partitionkey=hashed,
    )
    return topic, event


class ResultPublisher:
    """An `on_finished` callback that publishes each finished run's result in the background.
    Reads `state.ports` at publish time, so it follows the ports the lifespan built."""

    def __init__(self, state: State) -> None:
        self._state = state
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def ports(self) -> PortBundle:
        ports: PortBundle = self._state.ports
        return ports

    @property
    def pending(self) -> int:
        """Publishes started and not yet over."""
        return len(self._tasks)

    async def __call__(self, run: Run) -> None:
        if not run.done:
            return  # the run did not finish: there is no result to report
        topic, event = await result_event(run)
        task = asyncio.create_task(self._publish(topic, event), name=f"result-event:{event.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _publish(self, topic: str, event: CloudEvent) -> None:
        ports = self.ports
        telemetry = ports.telemetry
        try:
            await ports.events.publish(topic, event)
        except Exception as exc:  # never raised into a run; PublishFailed is the usual one
            telemetry.counter(PUBLISH_FAILED, topic=topic)
            telemetry.log(
                "warning",
                "result event not published",
                topic=topic,
                event_id=event.id,
                request_id=event.subject,
                error=type(exc).__name__,
                after_retries=isinstance(exc, PublishFailed),
            )
            return
        telemetry.counter(PUBLISHED, topic=topic)

    async def wait(self, timeout_s: float) -> int:
        """Wait for the pending publishes, up to `timeout_s`. Returns how many are still
        pending."""
        tasks = set(self._tasks)
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
        return sum(1 for task in tasks if not task.done())

    async def aclose(self, timeout_s: float | None = None) -> None:
        """Wait for pending publishes up to `timeout_s` (default `SHUTDOWN_WAIT_S`), then cancel
        and count the rest. Called before the ports close."""
        await self.wait(SHUTDOWN_WAIT_S if timeout_s is None else timeout_s)
        left = [task for task in self._tasks if not task.done()]
        for task in left:
            task.cancel()
        if left:
            await asyncio.gather(*left, return_exceptions=True)
            telemetry = self.ports.telemetry
            telemetry.counter(PUBLISH_ABANDONED, len(left))
            telemetry.log("warning", "result events abandoned at shutdown", count=len(left))
