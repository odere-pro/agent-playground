"""daprd next to a one-broker Kafka, for the `DaprEvents` binding. `network` tests only.

Both containers share one Docker network; Kafka has the alias `kafka`, so the PoC component files
(`deploy/compose/dapr/pubsub.yaml`, `resiliency.yaml`: brokers `kafka:9092`, app id `echo`, 2
retries) are mounted as they are. daprd calls the test's app back at
`host.docker.internal:<app_port>` (Docker Desktop reaches the host's loopback that way).

Two differences from the Compose pair, both because the test process is not in daprd's network
namespace: daprd listens on `0.0.0.0` (`--dapr-listen-addresses`) so its HTTP port can be
published, and the app channel is `host.docker.internal`, not `127.0.0.1`. Both tokens are set.

daprd reads `GET /dapr/subscribe` once, at start: `restart()` makes it read the list again.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from testcontainers.community.kafka import KafkaContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

from chassis_contracts.containers.kafka import KAFKA_IMAGE

DAPRD_IMAGE = "daprio/daprd:1.16.0"
"""The tag `deploy/compose/docker-compose.scale-dapr.yaml` pins (by digest there)."""
DAPR_HTTP_PORT = 3500
APP_ID = "echo"
"""The app id the PoC components are scoped to; it is also the Kafka consumer group."""


class DaprSidecar:
    """A running daprd: its endpoint (re-read after a restart), its health, and a restart."""

    def __init__(self, container: DockerContainer, api_token: str) -> None:
        self.container = container
        self._api_token = api_token

    @property
    def endpoint(self) -> str:
        host = self.container.get_container_host_ip()
        return f"http://{host}:{self.container.get_exposed_port(DAPR_HTTP_PORT)}"

    def wait_healthy(self, timeout_s: float = 60.0) -> None:
        deadline = time.monotonic() + timeout_s
        last = "no answer"
        while time.monotonic() < deadline:
            try:
                answer = httpx.get(f"{self.endpoint}/v1.0/healthz", timeout=2.0, trust_env=False)
                if answer.status_code == 204:
                    return
                last = f"healthz answered {answer.status_code}"
            except (httpx.HTTPError, ConnectionError) as exc:
                last = type(exc).__name__
            time.sleep(0.25)
        logs = self.container.get_logs()[0].decode(errors="replace")[-3000:]
        raise TimeoutError(f"daprd not healthy after {timeout_s} s ({last}); logs:\n{logs}")

    def metadata(self) -> dict[str, Any]:
        answer = httpx.get(
            f"{self.endpoint}/v1.0/metadata",
            headers={"dapr-api-token": self._api_token},
            timeout=5.0,
            trust_env=False,
        )
        answer.raise_for_status()
        loaded: dict[str, Any] = answer.json()
        return loaded

    def subscribed_topics(self) -> set[str]:
        return {item["topic"] for item in self.metadata().get("subscriptions") or []}

    def restart(self) -> None:
        self.container.get_wrapped_container().restart(timeout=5)
        self.wait_healthy()


@contextmanager
def dapr_with_kafka(
    components: Path,
    app_port: int,
    *,
    api_token: str,
    app_token: str,
    image: str = DAPRD_IMAGE,
) -> Iterator[DaprSidecar]:
    """Start Kafka and daprd on one network; yield the sidecar; remove both and the network."""
    with Network() as network:
        kafka = (
            KafkaContainer(KAFKA_IMAGE)
            .with_kraft()
            .with_network(network)
            .with_network_aliases("kafka")
            .with_env("KAFKA_AUTO_CREATE_TOPICS_ENABLE", "true")
            .with_env("KAFKA_NUM_PARTITIONS", "3")
            .with_env("KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1")
            .with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1")
            .with_env("KAFKA_HEAP_OPTS", "-Xms256m -Xmx512m")
        )
        with kafka:
            daprd = (
                DockerContainer(image)
                .with_network(network)
                .with_exposed_ports(DAPR_HTTP_PORT)
                .with_volume_mapping(str(components.resolve()), "/components", "ro")
                .with_env("DAPR_API_TOKEN", api_token)
                .with_env("APP_API_TOKEN", app_token)
                .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
                .with_command(
                    [
                        "./daprd",
                        "--app-id",
                        APP_ID,
                        "--app-port",
                        str(app_port),
                        "--app-channel-address",
                        "host.docker.internal",
                        "--dapr-http-port",
                        str(DAPR_HTTP_PORT),
                        "--dapr-listen-addresses",
                        "0.0.0.0",
                        "--resources-path",
                        "/components",
                        "--log-level",
                        "info",
                    ]
                )
            )
            with daprd:
                sidecar = DaprSidecar(daprd, api_token)
                sidecar.wait_healthy()
                yield sidecar
