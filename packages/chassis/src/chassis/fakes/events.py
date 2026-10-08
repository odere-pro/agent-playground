"""`InMemoryBus`: the `EventPort` fake. One process, one bus; two replicas in a test share one
object to stand for the broker. Same promise as Kafka: at least once, retries in place, then
`<topic>.dlq`, publish order per group, each group gets every event (a new group starts at the
first event of the topic, as Kafka's `auto_offset_reset="earliest"`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict

from chassis.ports.events import DLQ_SUFFIX, CloudEvent, EventHandler, PublishFailed


class _Subscription:
    def __init__(self, bus: InMemoryBus, task: asyncio.Task[None]) -> None:
        self._bus = bus
        self._task = task

    async def close(self) -> None:
        self._bus._subs.discard(self)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


class InMemoryBus:
    """In-memory `EventPort`. `published` records every `(topic, event)` taken, in order;
    `fail_next_publish` scripts broker failures."""

    def __init__(self, *, retry_delays_s: tuple[float, ...] = (0.0,)) -> None:
        self.published: list[tuple[str, CloudEvent]] = []
        self.retry_delays_s = retry_delays_s
        self._log: defaultdict[str, list[CloudEvent]] = defaultdict(list)
        self._queues: dict[tuple[str, str], asyncio.Queue[CloudEvent]] = {}
        self._subs: set[_Subscription] = set()
        self._fail_publishes = 0

    def fail_next_publish(self, times: int = 1) -> None:
        """The next `times` publishes raise `PublishFailed` and deliver nothing."""
        self._fail_publishes += times

    async def publish(self, topic: str, event: CloudEvent) -> None:
        if self._fail_publishes > 0:
            self._fail_publishes -= 1
            raise PublishFailed(f"scripted publish failure on {topic}")
        self.published.append((topic, event))
        self._log[topic].append(event)
        for (queue_topic, _group), queue in self._queues.items():
            if queue_topic == topic:
                queue.put_nowait(event)

    async def subscribe(
        self, topic: str, handler: EventHandler, *, group: str, max_attempts: int = 3
    ) -> _Subscription:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        queue = self._queues.get((topic, group))
        if queue is None:
            queue = asyncio.Queue()
            for event in self._log[topic]:
                queue.put_nowait(event)
            self._queues[(topic, group)] = queue
        task = asyncio.create_task(self._consume(topic, queue, handler, max_attempts))
        sub = _Subscription(self, task)
        self._subs.add(sub)
        return sub

    async def aclose(self) -> None:
        for sub in list(self._subs):
            await sub.close()

    async def _consume(
        self,
        topic: str,
        queue: asyncio.Queue[CloudEvent],
        handler: EventHandler,
        max_attempts: int,
    ) -> None:
        while True:
            event = await queue.get()
            try:
                await self._deliver(topic, event, handler, max_attempts)
            except asyncio.CancelledError:
                queue.put_nowait(event)  # not acknowledged: the group gets it again
                raise

    async def _deliver(
        self, topic: str, event: CloudEvent, handler: EventHandler, max_attempts: int
    ) -> None:
        for attempt in range(1, max_attempts + 1):
            try:
                await handler(event)
                return
            except Exception as exc:
                if attempt == max_attempts:
                    dead = event.model_copy(
                        update={
                            "deadletterreason": type(exc).__name__,
                            "deadletterattempts": max_attempts,
                        }
                    )
                    await self.publish(topic + DLQ_SUFFIX, dead)
                    return
                await asyncio.sleep(self.retry_delays_s[min(attempt, len(self.retry_delays_s)) - 1])
