"""`InMemoryBus` passes the `EventPort` contract suite, offline. `KafkaEvents` binds the same
suite in `tests/integration/test_kafka_events_contract.py`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from chassis.fakes import InMemoryBus
from chassis.ports.events import EventPort
from chassis_contracts.events import UNKNOWN_EXTENSION, EventPortContract, Settle, make_event

SETTLE_LOOP_TURNS = 100
"""The bus does no I/O and retries with `sleep(0)`, so all it can still do happens in loop turns,
and this count does not depend on machine load. Far more turns than one delivery or retry takes."""


class TestInMemoryBus(EventPortContract):
    @pytest.fixture
    async def event_port(self) -> AsyncIterator[EventPort]:
        bus = InMemoryBus()
        yield bus
        await bus.aclose()

    @pytest.fixture
    def broken_event_port(self) -> EventPort:
        bus = InMemoryBus()
        bus.fail_next_publish(times=1_000)
        return bus

    @pytest.fixture
    def settle(self) -> Settle:
        async def turn_the_loop() -> None:
            for _ in range(SETTLE_LOOP_TURNS):
                await asyncio.sleep(0)

        return turn_the_loop


async def test_published_records_every_event_and_fail_next_publish_fails_once() -> None:
    bus = InMemoryBus()
    bus.fail_next_publish()
    event = make_event("t.v1")
    with pytest.raises(Exception, match="scripted"):
        await bus.publish("t.v1", event)
    await bus.publish("t.v1", event)
    assert bus.published == [("t.v1", event)]
    await bus.aclose()


def test_kafka_from_env_needs_bootstrap_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    from chassis.adapters.kafka import KafkaEvents

    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    with pytest.raises(LookupError, match="KAFKA_BOOTSTRAP_SERVERS"):
        KafkaEvents.from_env()
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    port = KafkaEvents.from_env()
    assert (port.bootstrap_servers, port.security_protocol) == ("kafka:9092", "PLAINTEXT")


@pytest.mark.parametrize("adapter", ["kafka", "dapr"])
def test_each_broker_reader_accepts_and_drops_an_unknown_extension(adapter: str) -> None:
    """Contract v9 J.29 at each real reader's decode step: an event from a newer producer is
    read, not dropped as invalid, and its unknown extension is gone."""
    from chassis.adapters.dapr.events import _decode as dapr_decode
    from chassis.adapters.kafka.events import _decode as kafka_decode

    sent = make_event("t.v1")
    body = json.dumps({**json.loads(sent.to_wire()), UNKNOWN_EXTENSION: "x"}).encode()
    read = (kafka_decode if adapter == "kafka" else dapr_decode)(body)
    assert read is not None
    assert UNKNOWN_EXTENSION not in json.loads(read.to_wire())
    assert read.to_wire() == sent.to_wire()
