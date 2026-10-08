"""PoC-4 Compose stack for scale: `deploy/compose/docker-compose.scale.yaml` and its Dapr overlay.

Exit criterion 5 (docs/planning/poc/004-PoC-4-stateless-scalable.md): "Both containers run with a
read-only root file system on every engine, or the engine is flagged." The offline half: every
chassis, workload, and daprd service has `read_only: true`, a tmpfs on `/tmp` only, no Linux
capability, `no-new-privileges`, and a non-root user. One `x-workload` anchor serves every engine
(`WORKLOAD_IMAGE` picks it), so the check holds for all four. The live half is
`test_compose_scale.py::test_every_engine_answers_with_read_only_roots` (P16).

Exit criterion 4 ("throughput grows with replicas") needs explicit pairs behind one load
balancer: pair 1 always, pair 2 in `pairs2` and `pairs4`, pairs 3 and 4 in `pairs4`, and Traefik
lists all four with a health check on `/ready`.

Also SECURITY.md section 7: the only published port is Traefik's on the host's loopback, no
workload holds a key, and no secret value is written in any file. Parses YAML only; no Docker.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from chassis.server.config import load_config

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
SCALE = COMPOSE_DIR / "docker-compose.scale.yaml"
DAPR = COMPOSE_DIR / "docker-compose.scale-dapr.yaml"
TRAEFIK = COMPOSE_DIR / "traefik/poc04.yaml"
RESILIENCY = COMPOSE_DIR / "dapr/resiliency.yaml"
PUBSUB = COMPOSE_DIR / "dapr/pubsub.yaml"
SCRIPT = COMPOSE_DIR / "scale.sh"
CONFIGS = ROOT / "packages/chassis/configs"

PAIRS = (1, 2, 3, 4)
PAIR_PROFILES: dict[int, list[str] | None] = {
    1: None,
    2: ["pairs2", "pairs4"],
    3: ["pairs4"],
    4: ["pairs4"],
}
"""Pair number to its Compose profiles. Pair 1 has none: it always starts."""
UID = "10001:10001"
"""The non-root user and group of the chassis image and every workload image."""
WORKLOAD_ENV = {"CHASSIS_MODEL_URL", "CHASSIS_TOOL_URL", "HOST", "PORT", "DRAIN_TIMEOUT_MS"}
"""The four PoC-2 variables plus `DRAIN_TIMEOUT_MS` for the TypeScript image (plan, 8a)."""
PUBLISHED = "127.0.0.1:18080:8000"
"""Traefik's one port: the host's loopback 18080 (8080 is the PoC-1 stack's)."""
KEY_NAME = re.compile(r"(^|_)(KEY|TOKEN|SECRET|PASSWORD)(_|$)", re.IGNORECASE)
"""A key-like variable name, by whole word: `VALKEY_URL` is not one, `VALKEY_PASSWORD` is."""
REQUIRED = re.compile(r"\$\{[A-Z0-9_]+:\?[^}]*\}")
"""A secret is only ever `${NAME:?set by scale.sh}`: required, never a default, never a value."""
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
BUILT = "agent-platform/"
"""Images built from this repo by `scale.sh build`; every other image is pinned by digest."""


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} is not a mapping"
    return data


def _services(path: Path) -> dict[str, dict[str, Any]]:
    services = _load(path).get("services")
    assert isinstance(services, dict) and services, f"{path.relative_to(ROOT)}: no services"
    return services


def _env(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        pairs = [str(item).split("=", 1) for item in env]
        return {p[0]: (p[1] if len(p) > 1 else "") for p in pairs}
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def _hardened(name: str, service: dict[str, Any], user: str | None = UID) -> None:
    assert service.get("read_only") is True, f"{name}: the root file system must be read-only"
    assert service.get("tmpfs", []) in ([], ["/tmp"]), f"{name}: {service.get('tmpfs')}"
    assert service.get("cap_drop") == ["ALL"], f"{name}: {service.get('cap_drop')}"
    assert "cap_add" not in service, f"{name} adds a capability"
    assert service.get("security_opt") == ["no-new-privileges:true"], name
    if user is not None:
        assert service.get("user") == user, f"{name}: {service.get('user')}"
    assert not service.get("privileged"), f"{name} is privileged"


def test_scale_file_is_its_own_project_and_network() -> None:
    """Plan 8a: a file of its own, project `poc04` and network `poc04`, so no command on this
    stack ever names a container of another project.
    """
    data = _load(SCALE)
    assert data.get("name") == "poc04"
    assert data["networks"]["default"]["name"] == "poc04"
    expected = {
        "fake-model-server",
        "valkey",
        "minio",
        "minio-init",
        "kafka",
        "traefik",
        *(f"chassis-{n}" for n in PAIRS),
        *(f"workload-{n}" for n in PAIRS),
    }
    assert set(_services(SCALE)) == expected


@pytest.mark.parametrize("n", PAIRS)
def test_pairs_are_explicit_and_chosen_by_profile(n: int) -> None:
    """Exit criterion 4: 1, 2, or 4 pairs. Each workload joins its own chassis's namespace, and
    both halves of a pair carry the same profiles.
    """
    services = _services(SCALE)
    chassis, workload = services[f"chassis-{n}"], services[f"workload-{n}"]
    assert chassis.get("profiles") == PAIR_PROFILES[n], f"chassis-{n}"
    assert workload.get("profiles") == PAIR_PROFILES[n], f"workload-{n}"
    assert workload.get("network_mode") == f"service:chassis-{n}"
    assert f"chassis-{n}" in workload.get("depends_on", {})


def test_compose_sets_read_only_on_every_chassis_and_workload() -> None:
    """Exit criterion 5: both containers of every pair run with a read-only root, a tmpfs on
    `/tmp` only, no capability, `no-new-privileges`, and the images' non-root user. The workload
    anchor is the same for every engine, so this covers all four.
    """
    services = _services(SCALE)
    for n in PAIRS:
        for name in (f"chassis-{n}", f"workload-{n}"):
            _hardened(name, services[name])
            assert services[name].get("stop_grace_period") == "45s", name
            assert services[name].get("mem_limit") == "512m", name


def test_daprd_sidecars_are_hardened_and_follow_their_pair() -> None:
    """Exit criterion 5 for the Dapr variant: daprd is a third container in the pair's namespace,
    read-only and with no capability, in the same profiles as its pair.
    """
    services = _services(DAPR)
    for n in PAIRS:
        daprd = services[f"daprd-{n}"]
        _hardened(f"daprd-{n}", daprd, user=None)
        assert daprd.get("network_mode") == f"service:chassis-{n}"
        assert daprd.get("profiles") == PAIR_PROFILES[n], f"daprd-{n}"
        assert "ports" not in daprd, f"daprd-{n} publishes a port"


@pytest.mark.parametrize("path", [SCALE, DAPR], ids=["scale", "dapr"])
def test_only_traefik_publishes_and_only_on_loopback(path: Path) -> None:
    """SECURITY.md 7: the one published port is Traefik's, on 127.0.0.1. The chassis, the
    workloads, Valkey, MinIO, Kafka, and daprd publish nothing.
    """
    for name, service in _services(path).items():
        if name == "traefik":
            assert service.get("ports") == [PUBLISHED], service.get("ports")
        else:
            assert "ports" not in service, f"{name} publishes {service.get('ports')}"
        assert service.get("network_mode") != "host", name
        assert "env_file" not in service, f"{name} loads an env_file"
        volumes = [str(v) for v in service.get("volumes", [])]
        assert not [v for v in volumes if "docker.sock" in v], f"{name} mounts the Docker socket"


@pytest.mark.parametrize("n", PAIRS)
def test_no_key_in_any_workload_env(n: int) -> None:
    """ADR-001 hard requirement 1: the workload holds no key. Its env is the PoC-2 four plus
    `DRAIN_TIMEOUT_MS`, its model and tool URLs are the chassis's loopback proxies, and it binds
    loopback only.
    """
    workload = _services(SCALE)[f"workload-{n}"]
    env = _env(workload)
    assert set(env) <= WORKLOAD_ENV, f"workload-{n}: {sorted(env)}"
    assert [k for k in env if KEY_NAME.search(k)] == [], f"workload-{n} holds a key"
    for var, path in (("CHASSIS_MODEL_URL", "/v1"), ("CHASSIS_TOOL_URL", "/mcp")):
        url = urlsplit(env[var])
        assert (url.hostname, url.port, url.path) == ("127.0.0.1", 8090, path), env[var]
    assert env["HOST"] == "127.0.0.1"


@pytest.mark.parametrize("path", [SCALE, DAPR], ids=["scale", "dapr"])
def test_no_secret_value_in_any_file(path: Path) -> None:
    """Plan 8a, Secrets: no value in any file. Every key-like variable is a required
    interpolation that `scale.sh` fills, so a run without `scale.sh` fails instead of starting
    with a blank or a default secret.
    """
    for name, service in _services(path).items():
        for var, value in _env(service).items():
            if KEY_NAME.search(var):
                assert REQUIRED.fullmatch(value), f"{name}: {var} is not ${{{var}:?...}}"


@pytest.mark.parametrize("path", [SCALE, DAPR], ids=["scale", "dapr"])
def test_every_pulled_image_is_pinned_by_digest(path: Path) -> None:
    """SECURITY.md 2: every image this repo does not build is `name:tag@sha256:<digest>`."""
    for name, service in _services(path).items():
        image = str(service.get("image", ""))
        if image and BUILT not in image:
            assert DIGEST.search(image), f"{name}: {image}"


def test_traefik_balances_all_four_chassis_on_ready() -> None:
    """Exit criterion 4: Traefik reaches every chassis by name and drops one that fails `/ready`
    (a pair that is not running, draining, or with a hung workload). No retry middleware: the
    client's own retry is what the drills test.
    """
    http = _load(TRAEFIK)["http"]
    services = http["services"]
    (lb,) = (s["loadBalancer"] for s in services.values())
    urls = [s["url"] for s in lb["servers"]]
    assert urls == [f"http://chassis-{n}:8080" for n in PAIRS]
    assert lb["healthCheck"]["path"] == "/ready"
    assert lb["healthCheck"]["interval"] == "2s" and lb["healthCheck"]["timeout"] == "3s"
    assert "middlewares" not in http


def test_dapr_retries_match_the_subscribe_default() -> None:
    """Plan 2: Dapr owns retries and the dead-letter topic. `max_attempts=3` on `subscribe` is
    2 retries in the resiliency policy; the pub/sub component is Kafka on `kafka:9092`.
    """
    policies = _load(RESILIENCY)["spec"]["policies"]["retries"]
    assert [p["maxRetries"] for p in policies.values()] == [2]
    pubsub = _load(PUBSUB)
    assert pubsub["spec"]["type"] == "pubsub.kafka"
    meta = {m["name"]: m["value"] for m in pubsub["spec"]["metadata"]}
    assert meta["brokers"] == "kafka:9092"
    assert pubsub["metadata"]["name"] == "pubsub"


@pytest.mark.parametrize(
    ("name", "lane", "events"),
    [
        ("scale.yaml", "sidecar", "none"),
        ("scale-kafka.yaml", "sidecar", "kafka"),
        ("scale-dapr.yaml", "sidecar", "dapr"),
        ("scale-inprocess.yaml", "inprocess", "none"),
    ],
)
def test_scale_configs_name_the_real_adapters(name: str, lane: str, events: str) -> None:
    """Plan 8a: the bootstrap files the stack mounts. Each validates as `ChassisConfig`, keeps
    state in Valkey and the agent config in MinIO, and names its lane and event adapter.
    """
    config = load_config(CONFIGS / name)
    adapters = config.spec.adapters
    assert adapters is not None
    assert (adapters.model, adapters.config, adapters.state) == ("litellm", "minio", "valkey")
    assert adapters.events == events
    assert config.spec.engine.connector == lane
    if lane == "sidecar":
        assert config.spec.engine.url == "http://127.0.0.1:9000"
    assert config.spec.events.result_events is (events != "none")


def test_scale_script_never_touches_another_project() -> None:
    """Plan 8a: every Compose command in `scale.sh` names project `poc04` and the scale file, and
    nothing prunes or removes outside it.
    """
    text = SCRIPT.read_text()
    assert "docker system prune" not in text and "volume prune" not in text
    assert "-f docker-compose.scale.yaml" in text
    calls = [
        line.strip()
        for line in text.splitlines()
        if "docker compose" in line and not line.strip().startswith("#")
    ]
    assert calls, "scale.sh runs no Compose command"
    for call in calls:
        assert "docker compose -p poc04 " in call, call
