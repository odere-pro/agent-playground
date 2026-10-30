"""A one-broker Kafka (KRaft, no ZooKeeper) for the `KafkaEvents` binding. `network` tests only.

suggested: `auto.create.topics.enable=true` and 3 partitions per topic, as the PoC broker; a
512 MiB heap so the broker fits next to the other PoC-4 containers (about 1 GiB resident).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from testcontainers.community.kafka import KafkaContainer
from testcontainers.core.version import ComparableVersion

KAFKA_IMAGE = (
    "confluentinc/cp-kafka:7.6.0"
    "@sha256:24cdd3a7fa89d2bed150560ebea81ff1943badfa61e51d66bb541a6b0d7fb047"
)
"""The testcontainers 4.15 default image, pinned by digest (resolved 2026-10-29 with `docker
buildx imagetools inspect`). Not Compose's `apache/kafka`: `KafkaContainer` starts the broker
through cp-kafka's `/etc/confluent/docker/*` scripts, which `apache/kafka` does not have. The
intended difference is listed in `deploy/compose/SECURITY.md`, section 7.
"""


def kraft_version(image: str) -> str:
    """The tag of `image`, with any `@sha256:` digest dropped: `repo:7.6.0@sha256:..` -> `7.6.0`."""
    return image.split("@", 1)[0].rsplit(":", 1)[-1]


class PinnedKafkaContainer(KafkaContainer):
    """`KafkaContainer` whose KRaft check reads the tag, not the digest.

    testcontainers 4.15 takes the version after the last `:`; with a digest pin that is the hex
    digest, which is not a version. Same check, same minimum (`MIN_KRAFT_TAG`), on the tag.
    """

    def _verify_min_kraft_version(self) -> None:
        version = kraft_version(self.image)
        if ComparableVersion(version) < self.MIN_KRAFT_TAG:
            raise ValueError(f"Kafka {version} does not support KRaft (needs {self.MIN_KRAFT_TAG})")


@contextmanager
def kafka_container(image: str = KAFKA_IMAGE) -> Iterator[str]:
    """Start a broker, yield its bootstrap address (`host:port`), remove it on exit."""
    container = (
        PinnedKafkaContainer(image)
        .with_kraft()
        .with_env("KAFKA_AUTO_CREATE_TOPICS_ENABLE", "true")
        .with_env("KAFKA_NUM_PARTITIONS", "3")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1")
        .with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1")
        .with_env("KAFKA_HEAP_OPTS", "-Xms256m -Xmx512m")
    )
    with container:
        yield container.get_bootstrap_server()
