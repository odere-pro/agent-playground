"""PoC-1 walking skeleton: the Compose stack for the chassis and the router.

Exit criteria covered (docs/planning/poc/001-PoC-1-walking-skeleton.md):

- "`docker compose up` starts the chassis and the router with no manual steps."
- "Token counts per request are visible in the router."

The offline part reads `deploy/compose/**` and checks the rules in `deploy/compose/SECURITY.md`:
digests on every image, `127.0.0.1` on every published port, no host networking, health-gated
start order, no key in any file, and an empty `.env.example`. The live part brings the stack
up and sends one request; it needs Docker and sockets, so it is marked `network` and `slow`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = ROOT / "deploy/compose"
COMPOSE = COMPOSE_DIR / "docker-compose.yaml"
COMPOSE_LOCAL = COMPOSE_DIR / "docker-compose.local.yaml"
ENV_EXAMPLE = COMPOSE_DIR / ".env.example"
LITELLM_CONFIGS = sorted((COMPOSE_DIR / "litellm").glob("*.yaml"))
CHECKED_FILES = [COMPOSE, COMPOSE_LOCAL, *LITELLM_CONFIGS]

# A value that looks like an API key: an `sk-` prefix or 32+ hex characters in a row.
KEY_LIKE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}|\b[0-9a-f]{32,}\b")
# Image digests are 64 hex characters after `@sha256:`; they are not keys.
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
PUBLISHED = re.compile(r"^127\.0\.0\.1:(8080:8080|4000:4000)$")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} is not a mapping"
    return data


def _services(path: Path) -> dict[str, dict[str, Any]]:
    services = _load(path).get("services", {})
    assert isinstance(services, dict) and services, f"{path.relative_to(ROOT)}: no services"
    return services


def test_compose_files_exist() -> None:
    """Exit criterion: `docker compose up` starts the chassis and the router with no manual steps.
    The default file is the `fake` variant; the override adds the real models.
    """
    for path in (COMPOSE, COMPOSE_LOCAL, ENV_EXAMPLE, ROOT / "packages/chassis/Dockerfile"):
        assert path.exists(), f"missing {path.relative_to(ROOT)}"
    assert LITELLM_CONFIGS, "no LiteLLM config under deploy/compose/litellm"


@pytest.mark.parametrize("path", [COMPOSE, COMPOSE_LOCAL], ids=lambda p: p.name)
def test_every_image_is_pinned_by_digest(path: Path) -> None:
    """SECURITY.md section 2: every pulled `image:` is `name:tag@sha256:<digest>`. Images built
    from this repo (`build:`) pin their base in the Dockerfile instead; an override entry that
    only extends a base service names no image at all.
    """
    for name, service in _services(path).items():
        image = service.get("image")
        if image is None or "build" in service:
            continue
        assert DIGEST.search(image), f"{name}: image {image!r} is not pinned by digest"
        assert ":" in image.split("@")[0].rsplit("/", 1)[-1], f"{name}: image has no tag"


@pytest.mark.parametrize(
    "path",
    [ROOT / "packages/chassis/Dockerfile", ROOT / "packages/fake-model-server/Dockerfile"],
    ids=lambda p: p.parent.name,
)
def test_dockerfile_bases_are_pinned_by_digest(path: Path) -> None:
    """SECURITY.md section 2: the images built from this repo pin their `FROM` base by digest,
    and the chassis installs from `uv.lock` with hash checking (`uv sync --locked`).
    """
    text = path.read_text()
    bases = re.findall(r"^ARG \w+=(\S+)|^FROM (?!\$\{)(\S+)|^COPY --from=(\S+)", text, re.M)
    # A `COPY --from=<stage>` names a build stage, not an image; only registry refs have a `:`.
    refs = [ref for group in bases for ref in group if ref and ":" in ref]
    assert refs, f"{path.name}: no base image found"
    for ref in refs:
        assert DIGEST.search(ref), f"{path.name}: {ref!r} is not pinned by digest"
    assert "uv sync --locked --no-dev" in text, f"{path.name}: does not install from the lock"
    assert re.search(r"^USER \w+", text, re.M), f"{path.name}: runs as root"


@pytest.mark.parametrize("path", [COMPOSE, COMPOSE_LOCAL], ids=lambda p: p.name)
def test_published_ports_bind_loopback_only(path: Path) -> None:
    """SECURITY.md section 3: only `127.0.0.1:8080` and `127.0.0.1:4000` are published."""
    for name, service in _services(path).items():
        for port in service.get("ports", []):
            assert isinstance(port, str), f"{name}: write ports as strings"
            assert PUBLISHED.match(port), f"{name}: port {port!r} is not allowed"


@pytest.mark.parametrize("path", [COMPOSE, COMPOSE_LOCAL], ids=lambda p: p.name)
def test_no_host_network_or_privilege(path: Path) -> None:
    """SECURITY.md section 3: no `network_mode: host`, no `privileged`, no `extra_hosts`,
    no Docker socket mount.
    """
    for name, service in _services(path).items():
        assert service.get("network_mode") != "host", f"{name}: network_mode host"
        assert not service.get("privileged"), f"{name}: privileged"
        assert "extra_hosts" not in service, f"{name}: extra_hosts"
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume), f"{name}: mounts the Docker socket"


def test_chassis_waits_for_a_healthy_router() -> None:
    """Exit criterion: `docker compose up` starts the chassis and the router with no manual steps.
    Start order is health-gated, so `--wait` returns only when the chain is up.
    """
    services = _services(COMPOSE)
    chassis = services["chassis"]
    assert chassis["depends_on"]["litellm"] == {"condition": "service_healthy"}
    assert "healthcheck" in chassis and "healthcheck" in services["litellm"]
    probe = " ".join(chassis["healthcheck"]["test"])
    assert "/ready" in probe and "/health'" not in probe, "healthy means the engine is set up"
    litellm = services["litellm"]
    assert litellm["depends_on"]["fake-model-server"] == {"condition": "service_healthy"}


def test_keys_stay_in_env_and_reach_one_service_each() -> None:
    """SECURITY.md section 1: the provider key and the master key go to LiteLLM only; the chassis
    gets `LITELLM_API_KEY` only; nobody has `env_file: .env`.
    """
    for path in (COMPOSE, COMPOSE_LOCAL):
        for name, service in _services(path).items():
            assert "env_file" not in service, f"{name}: env_file hands every key to the service"
            env = service.get("environment", {})
            names = set(env) if isinstance(env, dict) else {e.split("=")[0] for e in env}
            if name == "chassis":
                # The override merges over the base, so the URL is checked on the base only.
                assert "LITELLM_API_KEY" in names
                assert path != COMPOSE or "LITELLM_BASE_URL" in names
                assert not names & {"LITELLM_MASTER_KEY", "OPENAI_API_KEY"}, f"{name}: {names}"
            elif name != "litellm":
                assert not {n for n in names if n.endswith("_KEY")}, f"{name}: {names}"


@pytest.mark.parametrize("path", CHECKED_FILES, ids=lambda p: str(p.relative_to(COMPOSE_DIR)))
def test_no_value_looks_like_a_key(path: Path) -> None:
    """SECURITY.md section 1: a literal that starts with `sk-` is a defect; `api_key: fake` is
    the one allowed placeholder.
    """
    text = DIGEST.sub("@sha256:<digest>", path.read_text())
    hits = [m.group(0) for m in KEY_LIKE.finditer(text)]
    assert hits == [], f"{path.relative_to(ROOT)}: key-like values {hits}"
    for line in text.splitlines():
        if "api_key" in line and "os.environ/" not in line:
            assert line.strip().endswith("api_key: fake"), f"{path.name}: {line.strip()}"


def test_env_example_has_every_variable_empty() -> None:
    """SECURITY.md section 1: `.env.example` is committed with every name and an empty value."""
    names: list[str] = []
    for line in ENV_EXAMPLE.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        assert value == "", f".env.example: {name} is not empty"
        names.append(name)
    assert {"LITELLM_MASTER_KEY", "LITELLM_API_KEY", "OPENAI_API_KEY"} <= set(names), names
    listed = subprocess.run(
        ["git", "ls-files", "deploy/compose"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert "deploy/compose/.env" not in listed, ".env is tracked"


def test_local_config_switches_model_adapter_to_litellm() -> None:
    """Exit criterion: switching the model adapter (`fake` or `litellm`) or the model route needs
    a config change only. The Compose stack mounts this file as the chassis config.
    """
    config = _load(ROOT / "packages/chassis/configs/local.yaml")
    assert config["profile"] == "local"
    assert config["spec"]["adapters"]["model"] == "litellm"
    assert config["spec"]["model"]["route"] == "big-default"
    assert "engine" not in config["spec"]["adapters"], "the lane is spec.engine.connector"
    assert config["spec"]["engine"]["connector"] == "inprocess", "no sidecar service in Compose yet"
    for name in ("litellm/config.yaml", "litellm/config.local.yaml"):
        routes = {m["model_name"] for m in _load(COMPOSE_DIR / name)["model_list"]}
        assert routes == {"big-default", "local-small"}, f"{name}: {routes}"


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *args],
        cwd=COMPOSE_DIR,
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "poc01-test"},
    )


def _sse_frames(lines: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    """`event: <name>` / `data: <json>` pairs, in order."""
    frames: list[tuple[str, dict[str, Any]]] = []
    name = ""
    for line in lines:
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            frames.append((name, json.loads(line[5:])))
    return frames


@pytest.mark.network
@pytest.mark.slow
def test_compose_up_serves_one_request_and_counts_tokens(request: pytest.FixtureRequest) -> None:
    """Exit criteria: `docker compose up` starts the chassis and the router with no manual steps;
    token counts per request are visible in the router; streaming and complete responses carry
    the same output for the same input. Needs Docker and sockets, so it is out of `make test`.

    One request goes chassis -> inprocess A2A -> echo_python -> chassis model proxy -> LiteLLM
    -> fake model server, whose `simplify` rule answers with 42 + 9 tokens.
    """
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run without --disable-socket to cover Compose")
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    _compose("up", "-d", "--wait", "--quiet-pull")
    try:
        health = httpx.get("http://127.0.0.1:8080/health", timeout=5.0)
        assert health.json() == {"status": "ok"}
        body = {"input": {"text": "simplify: the quick brown fox"}}
        answer = httpx.post("http://127.0.0.1:8080/v1/run", json=body, timeout=30.0).json()
        assert answer["status"] == "ok", answer
        assert answer["output"]["text"] == "Plain words. Short sentences. Same facts.", answer
        assert answer["metrics"]["input_tokens"] == 42, answer["metrics"]
        assert answer["metrics"]["output_tokens"] == 9, answer["metrics"]
        assert answer["versions"]["model_route"] == "big-default", answer["versions"]
        assert answer["versions"]["prompt"] == "simplifier-v1", answer["versions"]

        with httpx.stream(
            "POST", "http://127.0.0.1:8080/v1/run", json={**body, "stream": True}, timeout=30.0
        ) as streamed:
            assert streamed.status_code == 200
            frames = _sse_frames(streamed.iter_lines())
        deltas = "".join(data["text"] for name, data in frames if name == "delta")
        assert deltas == answer["output"]["text"], frames
        final = next(data for name, data in frames if name == "response")
        assert final["output"]["text"] == answer["output"]["text"], final

        deadline = time.monotonic() + 15
        logs = ""
        while time.monotonic() < deadline:
            logs = _compose("logs", "--no-color", "litellm", check=False).stdout
            if "token_log status=ok" in logs:
                break
            time.sleep(1)
        line = next((ln for ln in logs.splitlines() if "token_log status=ok" in ln), None)
        assert line, "the router did not log the token counts"
        assert "route=big-default" in line and "total_tokens=51" in line, line
        assert "tags=agent:echo" in line, line
        assert "quick brown fox" not in logs, "the router logged the prompt text"
        for name, forbidden in (("chassis", "OPENAI"), ("fake-model-server", "LITELLM")):
            env = _compose("exec", "-T", name, "env").stdout
            assert forbidden not in env, f"{name} sees {forbidden}: {json.dumps(env)}"
    finally:
        _compose("down", "-v", check=False)
