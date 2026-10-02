"""`KafkaEvents` passes the same `EventPort` contract suite as `InMemoryBus`, against a real
Kafka broker in a testcontainer. A `network` test: run it with `make test-integration`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from chassis.adapters.kafka import KafkaEvents
from chassis.ports.events import EventPort
from chassis_contracts.containers.kafka import kafka_container
from chassis_contracts.events import EventPortContract


@pytest.fixture(scope="module")
def kafka_bootstrap() -> Iterator[str]:
    with kafka_container() as bootstrap:
        yield bootstrap


class TestKafkaEvents(EventPortContract):
    @pytest.fixture
    async def event_port(self, kafka_bootstrap: str) -> AsyncIterator[EventPort]:
        port = KafkaEvents(kafka_bootstrap)
        yield port
        await port.aclose()

    @pytest.fixture
    async def broken_event_port(self) -> AsyncIterator[EventPort]:
        port = KafkaEvents("127.0.0.1:1", publish_backoff_s=(0.1, 0.1), request_timeout_ms=2_000)
        yield port
        await port.aclose()

    @pytest.fixture
    def deliver_timeout_s(self) -> float:
        return 30.0

    @pytest.fixture
    def quiet_s(self) -> float:
        return 2.0
