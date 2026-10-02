"""`InMemoryBus` passes the `EventPort` contract suite, offline. `KafkaEvents` binds the same
suite in `tests/integration/test_kafka_events_contract.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from chassis.fakes import InMemoryBus
from chassis.ports.events import EventPort
from chassis_contracts.events import EventPortContract, make_event


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
