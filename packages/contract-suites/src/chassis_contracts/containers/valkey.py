"""A throwaway Valkey in a container, for the `ValkeyState` binding (`network` tests only).

The password is made fresh per container and lives only in memory; it is never written to a
file or a log. Valkey keeps nothing on disk (`--save "" --appendonly no`).
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

VALKEY_IMAGE = "valkey/valkey:8-alpine"
"""suggested: the image the Compose stack uses (plan section 8a), without the digest pin."""
VALKEY_PORT = 6379
READY_LOG = "Ready to accept connections"


@dataclass(frozen=True)
class ValkeyServer:
    """Where the container listens. `url` holds no credential; pass `password` on its own."""

    url: str
    password: str = ""

    def __repr__(self) -> str:
        return f"ValkeyServer(url={self.url!r})"


@contextmanager
def valkey_container(image: str = VALKEY_IMAGE) -> Iterator[ValkeyServer]:
    """Start Valkey with a fresh password, yield it, and remove the container on exit."""
    password = secrets.token_urlsafe(24)
    container = (
        DockerContainer(image)
        .with_exposed_ports(VALKEY_PORT)
        .with_command(
            ["valkey-server", "--save", "", "--appendonly", "no", "--requirepass", password]
        )
        .waiting_for(LogMessageWaitStrategy(READY_LOG))
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(VALKEY_PORT)
        yield ValkeyServer(url=f"valkey://{host}:{port}/0", password=password)
