"""PoC-4 Compose scale stack: no container but Traefik has a way out (SECURITY.md, section 7).

The project network `poc04` is `internal: true`, so no chassis, workload, daprd, or backing
service reaches the internet, the LAN, or the host. Traefik alone also joins `edge`, a normal
network, so Docker can publish its one port on the host's loopback. A workload or daprd has no
network of its own: it shares its chassis's namespace (`network_mode: service:chassis-N`), so it
is on whatever its chassis is on.

Reads `docker-compose.scale.yaml` with the `docker-compose.scale-dapr.yaml` overlay merged on
top, as `scale.sh` passes them. Parses YAML only; no Docker. The live check (the stack starts and
`127.0.0.1:18080` answers) is `test_compose_scale.py` (`POC04_STACK=1`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
FILES = (
    COMPOSE_DIR / "docker-compose.scale.yaml",
    COMPOSE_DIR / "docker-compose.scale-dapr.yaml",
)
"""The files `scale.sh` passes, in order: the base, then the Dapr overlay."""
EDGE_SERVICE = "traefik"
"""The one service allowed on a network that is not internal."""
PUBLISHED = ["127.0.0.1:18080:8000"]
"""Traefik's one published port, unchanged by the network split."""
SHARED = "service:"
"""`network_mode` prefix: the container joins another service's namespace."""
DEFAULT = "default"

Doc = dict[str, Any]


def _load(path: Path) -> Doc:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} is not a mapping"
    return data


def _merged() -> Doc:
    """Top-level `networks` and `services` of every file, later files merged per service."""
    networks: Doc = {}
    services: dict[str, Doc] = {}
    for path in FILES:
        data = _load(path)
        networks = {**networks, **(data.get("networks") or {})}
        for name, service in (data.get("services") or {}).items():
            services = {**services, name: {**services.get(name, {}), **(service or {})}}
    return {"networks": networks, "services": services}


MERGED = _merged()
SERVICES: dict[str, Doc] = MERGED["services"]
NETWORKS: Doc = MERGED["networks"]


def _networks_of(name: str, seen: frozenset[str] = frozenset()) -> frozenset[str]:
    """The Compose networks a service is on; a shared namespace follows its owner."""
    assert name not in seen, f"network_mode loop through {name}"
    service = SERVICES[name]
    mode = service.get("network_mode")
    if mode is not None:
        assert str(mode).startswith(SHARED), f"{name}: network_mode {mode!r}"
        return _networks_of(str(mode).removeprefix(SHARED), seen | {name})
    joined = service.get("networks")
    if joined is None:
        return frozenset({DEFAULT})
    return frozenset(joined if isinstance(joined, list) else dict(joined))


def _is_internal(network: str) -> bool:
    return (NETWORKS.get(network) or {}).get("internal") is True


def test_poc04_network_is_internal_when_scale_files_are_merged() -> None:
    """The project network keeps its name `poc04` and is internal."""
    # Arrange
    project = NETWORKS.get(DEFAULT) or {}

    # Act
    name, internal = project.get("name"), project.get("internal")

    # Assert
    assert (name, internal) == ("poc04", True)


@pytest.mark.parametrize("service", sorted(set(SERVICES) - {EDGE_SERVICE}))
def test_service_joins_only_internal_networks_when_it_is_not_traefik(service: str) -> None:
    """Every service but Traefik, a shared namespace included, is on internal networks only."""
    # Arrange
    joined = _networks_of(service)

    # Act
    open_networks = sorted(n for n in joined if not _is_internal(n))

    # Assert
    assert joined, f"{service} is on no network"
    assert open_networks == [], f"{service} can leave the stack through {open_networks}"


def test_traefik_joins_poc04_and_one_open_network_when_scale_files_are_merged() -> None:
    """Traefik is on `poc04` to reach the chassis and on one open network to publish its port."""
    # Arrange
    joined = _networks_of(EDGE_SERVICE)

    # Act
    open_networks = sorted(n for n in joined if not _is_internal(n))

    # Assert
    assert DEFAULT in joined and _is_internal(DEFAULT)
    assert len(joined) == 2 and len(open_networks) == 1, sorted(joined)
    assert SERVICES[EDGE_SERVICE].get("ports") == PUBLISHED
