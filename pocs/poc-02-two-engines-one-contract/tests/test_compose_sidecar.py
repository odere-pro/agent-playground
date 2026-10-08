"""PoC-2 Compose stack for the `sidecar` lane: one workload container next to the chassis.

Exit criteria covered (docs/planning/poc/002-PoC-2-two-engines-one-contract.md):

- "No workload container holds a key. Each workload's model client points at the chassis model
  proxy." The container half: the overlay gives no workload a key, an `env_file`, or a port, and
  points its model and tool URLs at the chassis's loopback proxies.
- The demo: "The same request goes to four engines by swapping the workload container next to
  the chassis ... No workload container holds a key. The router shows the tokens for each."

The offline part reads `deploy/compose/docker-compose.sidecar.yaml` over the PoC-1 base and the
rules in `deploy/compose/SECURITY.md` (section 6). The live part runs
`deploy/compose/demo-sidecar.sh`; it needs Docker and sockets, so it is marked `network` and
`slow`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from chassis.adapters.a2a.sidecar import loopback_url

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
BASE = COMPOSE_DIR / "docker-compose.yaml"
OVERLAY = COMPOSE_DIR / "docker-compose.sidecar.yaml"
DEMO = COMPOSE_DIR / "demo-sidecar.sh"
DEMO_DIR = ROOT / "pocs/poc-02-two-engines-one-contract/demo"
CHASSIS_CONFIG_PATH = "/etc/chassis/config.yaml"

WORKLOADS = {
    "workload-python": "python",
    "workload-pydanticai": "pydanticai",
    "workload-langgraph": "langgraph",
    "workload-typescript": "typescript",
}
"""Service name to its one Compose profile."""
WORKLOAD_ENV = {"CHASSIS_MODEL_URL", "CHASSIS_TOOL_URL", "HOST", "PORT"}
"""The only variables a workload service sets."""
PROXY = ("127.0.0.1", 8090)
"""The chassis's localhost-only listener for the model proxy and the MCP endpoint."""
SIDECAR_PORT = 9000
KEY_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
WORKLOAD_UID = "10002"
"""The non-root user and group every workload image creates (`useradd --uid 10002`; the uid table
in deploy/README.md)."""
WORKLOAD_DOCKERFILES = {
    "workload-python": ROOT / "packages/workloads/echo-python/Dockerfile",
    "workload-pydanticai": ROOT / "packages/workloads/echo-pydanticai/Dockerfile",
    "workload-langgraph": ROOT / "packages/workloads/echo-langgraph/Dockerfile",
    "workload-typescript": ROOT / "packages/workloads/echo-typescript/Dockerfile",
}


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


def _mounted_config() -> Path:
    """The host file the overlay mounts at the chassis's config path."""
    for volume in _services(OVERLAY)["chassis"].get("volumes", []):
        source, _, rest = str(volume).partition(":")
        if rest.split(":")[0] == CHASSIS_CONFIG_PATH:
            return (COMPOSE_DIR / source).resolve()
    raise AssertionError(f"the overlay mounts nothing at {CHASSIS_CONFIG_PATH}")


def test_overlay_parses_and_runs_four_workloads() -> None:
    """Demo: the same request goes to four engines by swapping the workload container next to
    the chassis. The overlay names the chassis and exactly the four workload services, and its
    own project name, so it never collides with a running PoC-1 stack.
    """
    data = _load(OVERLAY)
    assert data.get("name") == "poc02"
    assert set(_services(OVERLAY)) == {"chassis", *WORKLOADS}
    assert data["networks"]["default"]["name"] == "poc02"


@pytest.mark.parametrize("name", sorted(WORKLOADS))
def test_workload_shares_the_chassis_namespace_and_holds_no_key(name: str) -> None:
    """Exit criterion: no workload container holds a key. Each workload's model client points at
    the chassis model proxy. The workload joins the chassis's network namespace, publishes no
    port, loads no `env_file`, sets only the four allowed variables, and its model and tool URLs
    are the chassis's loopback proxies.
    """
    service = _services(OVERLAY)[name]
    assert service.get("network_mode") == "service:chassis", name
    assert "ports" not in service and "expose" not in service, f"{name} opens a port"
    assert "networks" not in service, f"{name} must not join a network of its own"
    assert "env_file" not in service, f"{name} loads an env_file"
    assert not service.get("privileged") and "extra_hosts" not in service, name
    env = _env(service)
    assert set(env) == WORKLOAD_ENV, f"{name}: {sorted(env)}"
    assert [k for k in env if KEY_NAME.search(k)] == [], f"{name} holds a key"
    for var, path in (("CHASSIS_MODEL_URL", "/v1"), ("CHASSIS_TOOL_URL", "/mcp")):
        url = urlsplit(env[var])
        assert (url.scheme, url.hostname, url.port, url.path) == ("http", *PROXY, path), env[var]
    assert env["HOST"] == "127.0.0.1", f"{name} must listen on localhost only"
    assert env["PORT"] == str(SIDECAR_PORT)


@pytest.mark.parametrize("name", sorted(WORKLOADS))
def test_workload_runs_hardened(name: str) -> None:
    """SECURITY.md section 6: a workload container drops every capability, cannot gain new
    privileges, has a read-only root file system (a `tmpfs` on `/tmp` only), and runs as the
    images' non-root user `10002:10002`.
    """
    service = _services(OVERLAY)[name]
    assert service.get("cap_drop") == ["ALL"], f"{name}: {service.get('cap_drop')}"
    assert "cap_add" not in service, f"{name} adds a capability"
    assert service.get("security_opt") == ["no-new-privileges:true"], name
    assert service.get("read_only") is True, f"{name}: the root file system must be read-only"
    assert service.get("tmpfs", []) in ([], ["/tmp"]), f"{name}: {service.get('tmpfs')}"
    assert service.get("user") == f"{WORKLOAD_UID}:{WORKLOAD_UID}", f"{name}: {service.get('user')}"


@pytest.mark.parametrize("name", sorted(WORKLOADS))
def test_compose_user_matches_the_image_user(name: str) -> None:
    """SECURITY.md section 6: the overlay's `user:` is the uid and gid the image creates and the
    image's numeric `USER`, so nothing runs as root. The app code is root-owned and read-only.
    """
    text = WORKLOAD_DOCKERFILES[name].read_text()
    assert re.search(rf"groupadd --gid {WORKLOAD_UID} workload\b", text), name
    assert re.search(rf"useradd --uid {WORKLOAD_UID} --gid workload\b", text), name
    assert re.search(rf"^USER {WORKLOAD_UID}:{WORKLOAD_UID}$", text, re.M), name


def test_litellm_uses_the_bundled_cost_map() -> None:
    """SECURITY.md section 5: LiteLLM reads its bundled model cost map and downloads none at run
    time. Set on the base service; an overlay that redefines `litellm` keeps it too.
    """
    for path in (BASE, OVERLAY, COMPOSE_DIR / "docker-compose.local.yaml"):
        litellm = _services(path).get("litellm")
        if litellm is None or (path != BASE and "environment" not in litellm):
            continue
        env = _env(litellm)
        assert env.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True", f"{path.name}: {env}"


def test_overlay_has_no_stale_port_note() -> None:
    """The echo-typescript image defaults `CHASSIS_MODEL_URL` to the proxy on 8090; no comment
    in the overlay or SECURITY.md says it is the public port 8080.
    """
    for path in (OVERLAY, COMPOSE_DIR / "SECURITY.md"):
        assert "127.0.0.1:8080/v1" not in path.read_text(), path.name


@pytest.mark.parametrize("name", sorted(WORKLOADS))
def test_workload_healthcheck_reads_the_agent_card(name: str) -> None:
    """Demo: the swap is health-gated. Healthy means the workload serves its agent card."""
    probe = " ".join(map(str, _services(OVERLAY)[name]["healthcheck"]["test"]))
    assert "/.well-known/agent-card.json" in probe, probe
    assert "127.0.0.1" in probe, probe


def test_one_profile_per_workload() -> None:
    """Demo: exactly one workload runs at a time. Each workload has one profile of its own; the
    chassis, the router, and the fake model server have none, so they always start.
    """
    services = _services(OVERLAY)
    for name, profile in WORKLOADS.items():
        assert services[name].get("profiles") == [profile], name
    assert len(set(WORKLOADS.values())) == len(WORKLOADS)
    for name, service in {**_services(BASE), "chassis": services["chassis"]}.items():
        assert "profiles" not in service, f"{name} must start in every profile"


def test_chassis_is_the_only_holder_of_the_key_and_the_port() -> None:
    """Exit criterion: no workload container holds a key. The overlay adds no key and no port to
    the chassis (the base's `LITELLM_API_KEY` and `127.0.0.1:8080` stay the only ones) and gives
    it no `env_file`.
    """
    chassis = _services(OVERLAY)["chassis"]
    assert "ports" not in chassis, "the base publishes 127.0.0.1:8080; the overlay adds none"
    assert "env_file" not in chassis
    assert [k for k in _env(chassis) if KEY_NAME.search(k)] == []
    base = _env(_services(BASE)["chassis"])
    assert "LITELLM_API_KEY" in base
    assert _services(BASE)["chassis"]["ports"] == ["127.0.0.1:8080:8080"]


def test_chassis_public_port_leaves_localhost_to_the_proxies() -> None:
    """deploy/CLAUDE.md: the chassis's public port binds to the pod IP, so localhost carries only
    the proxies. In the shared namespace the chassis serves on the container's own address, not
    `0.0.0.0` or `127.0.0.1`, and its healthcheck probes that address.
    """
    chassis = _services(OVERLAY)["chassis"]
    script = " ".join(map(str, chassis["entrypoint"]))
    assert "chassis serve" in script and "--host" in script, script
    assert "0.0.0.0" not in script, "0.0.0.0 would put the public port on localhost too"
    assert "gethostbyname" in script, "bind to the container's own address"
    assert "--proxy-host" not in script, "the proxies keep their loopback default"
    command = list(map(str, chassis["command"]))
    assert command[command.index("--config") + 1] == CHASSIS_CONFIG_PATH
    assert "0.0.0.0" not in command and "--host" not in command, command
    probe = " ".join(map(str, chassis["healthcheck"]["test"]))
    assert "/ready" in probe and "gethostbyname" in probe, probe


def test_chassis_binds_first_and_waits_for_the_sidecar_through_ready() -> None:
    """Demo: `up --wait` starts the stack with no manual step. The workload joins the chassis's
    namespace, so the chassis container must start first. PoC-5 (H14) removed the entrypoint's
    card wait: `chassis serve` binds its proxy first, so a workload can never take 8090 before it,
    and keeps `/ready` 503 `starting` until the sidecar answers (`--startup-wait-s`). The
    healthcheck probes `/ready` with a start period that covers that wait. There is no restart
    policy (a restarted chassis gets a new namespace and strands the workload).
    """
    chassis = _services(OVERLAY)["chassis"]
    script = " ".join(map(str, chassis["entrypoint"]))
    assert "agent-card" not in script, "no card wait before the bind (H14)"
    assert "urlopen" not in script and "sleep" not in script, script
    assert "exec chassis serve" in script, script
    health = chassis["healthcheck"]
    probe = " ".join(map(str, health["test"]))
    assert ":8080/ready" in probe, probe
    assert "agent-card" not in probe, "the chassis is healthy by its own `/ready`"
    start_period = str(health["start_period"])
    assert start_period.endswith("s") and int(start_period[:-1]) >= 120, start_period
    assert "restart" not in chassis
    for name in WORKLOADS:
        assert "restart" not in _services(OVERLAY)[name], name
    # Compose interpolates `$`; a shell variable in the entrypoint must be written `$$`.
    assert not re.search(r"(?<!\$)\$(?!\$)", script), "unescaped $ in the chassis entrypoint"


def test_mounted_config_is_the_sidecar_lane_on_loopback() -> None:
    """Exit criterion: each workload's model client points at the chassis model proxy. The
    chassis config the overlay mounts picks `connector: sidecar` with a loopback `url` on the
    workload's port, the real router, and the fake tools the MCP endpoint serves.
    """
    path = _mounted_config()
    assert path == ROOT / "packages/chassis/configs/sidecar.yaml", path
    config = _load(path)
    assert config["profile"] == "local"
    spec = config["spec"]
    assert spec["engine"]["connector"] == "sidecar"
    assert "handle" not in spec["engine"], "the sidecar lane loads no handle in the chassis"
    url = loopback_url(spec["engine"]["url"])
    assert urlsplit(url).port == SIDECAR_PORT, url
    assert spec["adapters"]["model"] == "litellm"
    assert spec["adapters"]["tools"] == "fake"


def test_every_build_names_an_existing_dockerfile() -> None:
    """Demo: every image in the stack builds from this repo. Each `build` has a context that
    exists and a Dockerfile inside it.
    """
    for path in (BASE, OVERLAY):
        for name, service in _services(path).items():
            build = service.get("build")
            if build is None:
                continue
            context = (COMPOSE_DIR / build["context"]).resolve()
            assert context.is_dir(), f"{path.name}: {name}: no context {context}"
            dockerfile = context / build.get("dockerfile", "Dockerfile")
            assert dockerfile.is_file(), f"{path.name}: {name}: no {dockerfile}"
            assert "image" in service, f"{path.name}: {name}: name the built image"
    for name in WORKLOADS:
        assert "build" in _services(OVERLAY)[name], f"{name} must build its own image"


def test_echo_python_image_is_pinned_and_locked() -> None:
    """SECURITY.md section 2: the new echo-python image pins its base and uv by the same digests
    as the chassis, installs from `uv.lock`, never installs the chassis, and runs as a user.
    """
    text = (ROOT / "packages/workloads/echo-python/Dockerfile").read_text()
    chassis = (ROOT / "packages/chassis/Dockerfile").read_text()
    refs = re.findall(r"^ARG \w+=(\S+)|^COPY --from=(\S+:\S+?) ", text, re.M)
    refs_flat = [r for pair in refs for r in pair if r]
    assert len(refs_flat) == 2, refs_flat
    for ref in refs_flat:
        assert DIGEST.search(ref), ref
        assert ref in chassis, f"{ref} differs from the chassis's pin"
    assert "uv sync --locked --no-dev --package echo-python --package workload-a2a" in text
    assert "--package chassis" not in text
    assert re.search(rf"^USER {WORKLOAD_UID}:{WORKLOAD_UID}$", text, re.M)
    assert "workload-a2a serve --handle echo_python:handle" in text


def test_demo_script_swaps_all_four_and_writes_the_record() -> None:
    """Demo: the script runs every workload profile, sends a `glossary` request complete and
    streamed, prints the router's token lines and each workload's key-like env, and writes its
    record to the PoC-2 `demo/` folder.
    """
    text = DEMO.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    for profile in WORKLOADS.values():
        assert re.search(rf"\b{profile}\b", text), profile
    for needle in (
        "docker-compose.sidecar.yaml",
        "glossary",
        "token_log",
        "grep -i -E 'key|token|secret'",
        "pocs/poc-02-two-engines-one-contract/demo",
        "/ready",
        " down",
    ):
        assert needle in text, needle
    assert re.search(r"stream\W+true", text), "no streamed request"
    assert DEMO_DIR.is_dir()


def test_demo_script_fails_on_the_chassis_key_in_any_log() -> None:
    """SECURITY.md, "Tests that prove it": no key in any log. The script reads the chassis key
    from the `.env` next to itself (Compose reads it there; the live test passes no key in the
    shell), never echoes it, counts it in the logs with a fixed-string grep, and fails on a count
    above 0. The check is not skipped when the shell has no `LITELLM_API_KEY`.
    """
    text = DEMO.read_text()
    assert re.search(r'^  say "No key in any log"$', text, re.M), "the check runs in main"
    gate = r'if \[\[ -n "\$\{LITELLM_API_KEY:-\}" \]\]; then\s+say "No key in any log"'
    assert not re.search(gate, text), "the check must not need the key in the shell"
    assert re.search(r'ENV_FILE="?\$HERE/\.env"?', text), "find .env relative to the script"
    assert re.search(r'^HERE=\$\(cd "\$\(dirname "\$0"\)" && pwd\)$', text, re.M), "HERE"
    assert "set +x" in text
    assert re.search(r'grep -c -F -e "\$chassis_key"', text), "fixed-string count of the key"
    assert re.search(r"key_count.*-gt 0", text), "a count above 0 must fail the script"
    for line in text.splitlines():
        if "chassis_key" in line and re.search(r"\b(printf|echo)\b", line):
            raise AssertionError(f"the key may be printed: {line.strip()}")


@pytest.mark.network
@pytest.mark.slow
def test_demo_runs_every_workload_with_no_key(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """Demo and exit criterion: the same request goes to four engines by swapping the workload
    container next to the chassis; no workload container holds a key; the router shows the
    tokens for each. Needs Docker and sockets, so it is out of `make test`.
    """
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run without --disable-socket to cover Compose")
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if info.returncode != 0:
        pytest.skip(f"docker info failed, the daemon is not running: {info.stderr.strip()[:200]}")
    out = tmp_path / "demo.md"
    result = subprocess.run(
        ["bash", str(DEMO)],
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
        env={"PATH": os.environ["PATH"], "HOME": str(Path.home()), "DEMO_OUT": str(out)},
    )
    record = out.read_text() if out.exists() else result.stdout
    assert result.returncode == 0, (result.stderr + record)[-4000:]
    for profile in WORKLOADS.values():
        assert f"## Workload {profile}: " in record, profile
    assert record.count("key-like variables in the workload: none") == len(WORKLOADS), record
    assert record.count("token_log status=ok") >= len(WORKLOADS), record
    assert "(no token_log line)" not in record, record
