"""A one-broker Kafka (KRaft, no ZooKeeper) for the `KafkaEvents` binding. `network` tests only.

suggested: `auto.create.topics.enable=true` and 3 partitions per topic, as the PoC broker; a
512 MiB heap so the broker fits next to the other PoC-4 containers (about 1 GiB resident).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from testcontainers.community.kafka import KafkaContainer

KAFKA_IMAGE = "confluentinc/cp-kafka:7.6.0"
"""The testcontainers 4.15 default image; 7.x supports KRaft."""


@contextmanager
def kafka_container(image: str = KAFKA_IMAGE) -> Iterator[str]:
    """Start a broker, yield its bootstrap address (`host:port`), remove it on exit."""
    container = (
        KafkaContainer(image)
        .with_kraft()
        .with_env("KAFKA_AUTO_CREATE_TOPICS_ENABLE", "true")
        .with_env("KAFKA_NUM_PARTITIONS", "3")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1")
        .with_env("KAFKA_HEAP_OPTS", "-Xms256m -Xmx512m")
    )
    with container:
        yield container.get_bootstrap_server()
