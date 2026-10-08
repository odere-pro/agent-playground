"""EventPortContract: CloudEvents 1.0 at least once, in order per partition key, retried, then
dead-lettered to `<topic>.dlq`; each group gets every event.

Every event a case receives is checked against the CloudEvents 1.0 JSON Schema, vendored in
`data/cloudevents-1.0.schema.json`, plus the two rules the schema cannot say: attribute names are
lowercase letters and digits, and `time` is RFC 3339 with an offset.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from chassis.ports.events import (
    CONTENT_TYPE,
    DLQ_SUFFIX,
    CloudEvent,
    EventPort,
    PublishFailed,
)

SCHEMA_PATH = Path(__file__).parent / "data" / "cloudevents-1.0.schema.json"
ATTRIBUTE_NAME = re.compile(r"^[a-z0-9]{1,20}$")
"""CloudEvents 1.0: attribute names are lowercase a-z and 0-9, 20 characters at most."""
SECRET_TEXT = "secret-detail-do-not-forward"
"""The failing handler's message. It must never reach the dead-letter event."""


class HandlerFailed(Exception):
    """The scripted handler failure the retry and dead-letter cases raise."""


@cache
def cloudevents_schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    return loaded


def assert_valid_cloudevent(event: CloudEvent) -> dict[str, Any]:
    """Check the wire form of `event`; return it parsed."""
    wire: dict[str, Any] = json.loads(event.to_wire())
    jsonschema.validate(wire, cloudevents_schema())
    assert wire["specversion"] == "1.0"
    for name in wire:
        assert ATTRIBUTE_NAME.match(name), f"bad CloudEvents attribute name {name!r}"
    parsed = datetime.fromisoformat(wire["time"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, "time must carry an offset (RFC 3339)"
    return wire


def make_event(topic: str, n: int = 0, *, partitionkey: str | None = None) -> CloudEvent:
    return CloudEvent(
        id=uuid.uuid4().hex,
        source="/agents/contract-suite",
        type=topic,
        subject=f"req-{n}",
        time=datetime.now(UTC),
        data={"n": n},
        partitionkey=partitionkey,
    )


class Inbox:
    """A handler that records what it gets and fails the first `fail` deliveries of each id."""

    def __init__(self, *, fail: int = 0) -> None:
        self.fail = fail
        self.received: list[CloudEvent] = []
        self._tries: dict[str, int] = {}

    async def __call__(self, event: CloudEvent) -> None:
        assert_valid_cloudevent(event)
        self.received.append(event)
        tries = self._tries.get(event.id, 0) + 1
        self._tries[event.id] = tries
        if tries <= self.fail:
            raise HandlerFailed(SECRET_TEXT)

    def ids(self) -> list[str]:
        return [event.id for event in self.received]


async def wait_until(check: Callable[[], bool], timeout_s: float, what: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(f"timed out after {timeout_s} s waiting for {what}")
        await asyncio.sleep(0.02)


@pytest.mark.contract
class EventPortContract:
    """Subclass as `Test*`. Provide `event_port`; optionally `broken_event_port` (a port whose
    broker is down) and a longer `deliver_timeout_s` for a real broker."""

    @pytest.fixture
    def event_port(self) -> EventPort:
        raise NotImplementedError("provide an event_port fixture")

    @pytest.fixture
    def broken_event_port(self) -> EventPort:
        pytest.skip("override broken_event_port with a port whose broker is down")

    @pytest.fixture
    def topic(self) -> str:
        return f"test.{uuid.uuid4().hex}.event.v1"

    @pytest.fixture
    def deliver_timeout_s(self) -> float:
        return 5.0

    @pytest.fixture
    def quiet_s(self) -> float:
        """How long to watch for an event that must not arrive."""
        return 0.3

    async def test_a_published_event_reaches_a_subscriber(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float
    ) -> None:
        inbox = Inbox()
        sub = await event_port.subscribe(topic, inbox, group=f"{topic}.g")
        event = make_event(topic)
        await event_port.publish(topic, event)
        await wait_until(lambda: len(inbox.received) >= 1, deliver_timeout_s, "the event")
        await sub.close()
        assert inbox.ids() == [event.id]
        assert inbox.received[0].data == {"n": 0}

    async def test_every_published_event_is_a_valid_cloudevent_with_its_extensions(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float
    ) -> None:
        assert CONTENT_TYPE == "application/cloudevents+json"
        inbox = Inbox()
        sub = await event_port.subscribe(topic, inbox, group=f"{topic}.g")
        event = make_event(topic, partitionkey="idem-1").model_copy(
            update={
                "dataschema": "task-result.v1.json",
                "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                "idempotencykey": "idem-1",
                "configversion": "cfg-7",
                "modelroute": "small",
            }
        )
        await event_port.publish(topic, event)
        await wait_until(lambda: len(inbox.received) >= 1, deliver_timeout_s, "the event")
        await sub.close()
        wire = assert_valid_cloudevent(inbox.received[0])
        assert wire == json.loads(event.to_wire())
        for name in ("traceparent", "idempotencykey", "configversion", "modelroute"):
            assert wire[name] == getattr(event, name)
        assert wire["partitionkey"] == "idem-1"

    async def test_events_with_one_partition_key_keep_their_order(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float
    ) -> None:
        inbox = Inbox()
        sub = await event_port.subscribe(topic, inbox, group=f"{topic}.g")
        events = [make_event(topic, n, partitionkey="one-key") for n in range(20)]
        for event in events:
            await event_port.publish(topic, event)
        await wait_until(lambda: len(inbox.received) >= 20, deliver_timeout_s, "20 events")
        await sub.close()
        assert inbox.ids() == [event.id for event in events]

    async def test_a_failing_handler_gets_the_event_again(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float
    ) -> None:
        inbox = Inbox(fail=1)
        sub = await event_port.subscribe(topic, inbox, group=f"{topic}.g", max_attempts=3)
        event = make_event(topic)
        await event_port.publish(topic, event)
        await wait_until(lambda: len(inbox.received) >= 2, deliver_timeout_s, "the redelivery")
        await sub.close()
        assert inbox.ids() == [event.id, event.id]

    async def test_after_max_attempts_the_event_goes_to_the_dead_letter_topic(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float, quiet_s: float
    ) -> None:
        failing = Inbox(fail=100)
        dead = Inbox()
        dlq = topic + DLQ_SUFFIX
        dead_sub = await event_port.subscribe(dlq, dead, group=f"{topic}.dlq.g")
        sub = await event_port.subscribe(topic, failing, group=f"{topic}.g", max_attempts=2)
        event = make_event(topic, partitionkey="k")
        await event_port.publish(topic, event)
        await wait_until(lambda: len(dead.received) >= 1, deliver_timeout_s, "the dead letter")
        await asyncio.sleep(quiet_s)
        await sub.close()
        await dead_sub.close()
        assert failing.ids() == [event.id, event.id]
        wire = assert_valid_cloudevent(dead.received[0])
        assert wire["id"] == event.id
        assert wire["data"] == event.data
        assert wire["deadletterattempts"] == 2
        assert wire["deadletterreason"] == "HandlerFailed"
        assert SECRET_TEXT not in dead.received[0].to_wire().decode()

    async def test_two_groups_each_get_every_event(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float
    ) -> None:
        first, second = Inbox(), Inbox()
        sub1 = await event_port.subscribe(topic, first, group=f"{topic}.g1")
        sub2 = await event_port.subscribe(topic, second, group=f"{topic}.g2")
        events = [make_event(topic, n, partitionkey="k") for n in range(3)]
        for event in events:
            await event_port.publish(topic, event)
        await wait_until(
            lambda: len(first.received) >= 3 and len(second.received) >= 3,
            deliver_timeout_s,
            "both groups",
        )
        await sub1.close()
        await sub2.close()
        want = [event.id for event in events]
        assert first.ids() == want
        assert second.ids() == want

    async def test_a_closed_subscription_gets_nothing_more(
        self, event_port: EventPort, topic: str, deliver_timeout_s: float, quiet_s: float
    ) -> None:
        inbox = Inbox()
        sub = await event_port.subscribe(topic, inbox, group=f"{topic}.g")
        before = make_event(topic, 0, partitionkey="k")
        await event_port.publish(topic, before)
        await wait_until(lambda: len(inbox.received) >= 1, deliver_timeout_s, "the first event")
        await sub.close()
        await event_port.publish(topic, make_event(topic, 1, partitionkey="k"))
        await asyncio.sleep(quiet_s)
        assert inbox.ids() == [before.id]

    async def test_publish_to_a_broken_broker_raises_publish_failed(
        self, broken_event_port: EventPort, topic: str
    ) -> None:
        with pytest.raises(PublishFailed):
            await broken_event_port.publish(topic, make_event(topic))
