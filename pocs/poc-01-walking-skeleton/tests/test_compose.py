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
from collections.abc import Callable, Iterable, Iterator
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
CHASSIS_URL = "http://127.0.0.1:8080"
SIMPLIFIED = "Plain words. Short sentences. Same facts."
RUN_BODY: dict[str, Any] = {"input": {"text": "simplify: the quick brown fox"}}
LOG_TIMEOUT_S = 15.0


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} is not a mapping"
    return data


def _services(path: Path) -> dict[str, dict[str, Any]]:
    services = _load(path).get("services", {})
    assert isinstance(services, dict) and services, f"{path.relative_to(ROOT)}: no services"
    return services


def _env_names(service: dict[str, Any]) -> set[str]:
    env = service.get("environment", {})
    return set(env) if isinstance(env, dict) else {e.split("=")[0] for e in env}


def _service_params(
    keep: Callable[[str, dict[str, Any]], bool] = lambda name, service: True,
) -> list[Any]:
    """One `pytest.param(path, name, service)` per service in both Compose files that `keep`s."""
    return [
        pytest.param(path, name, service, id=f"{path.name}-{name}")
        for path in (COMPOSE, COMPOSE_LOCAL)
        for name, service in _services(path).items()
        if keep(name, service)
    ]


EVERY_SERVICE = _service_params()
PULLED_IMAGES = _service_params(lambda name, s: s.get("image") is not None and "build" not in s)
NOT_CHASSIS_OR_ROUTER = _service_params(lambda name, s: name not in {"chassis", "litellm"})
DOCKERFILES = pytest.mark.parametrize(
    "path",
    [ROOT / "packages/chassis/Dockerfile", ROOT / "packages/fake-model-server/Dockerfile"],
    ids=lambda p: p.parent.name,
)


@pytest.mark.parametrize(
    "path",
    [COMPOSE, COMPOSE_LOCAL, ENV_EXAMPLE, ROOT / "packages/chassis/Dockerfile"],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_compose_files_exist(path: Path) -> None:
    """Exit criterion: `docker compose up` starts the chassis and the router with no manual steps.
    The default file is the `fake` variant; the override adds the real models.
    """
    assert path.exists(), f"missing {path.relative_to(ROOT)}"


def test_litellm_config_exists_under_deploy_compose() -> None:
    """Exit criterion: `docker compose up` starts the chassis and the router with no manual steps.
    The router's config ships with the stack.
    """
    assert LITELLM_CONFIGS, "no LiteLLM config under deploy/compose/litellm"


@pytest.mark.parametrize(("path", "name", "service"), PULLED_IMAGES)
def test_every_image_is_pinned_by_digest(path: Path, name: str, service: dict[str, Any]) -> None:
    """SECURITY.md section 2: every pulled `image:` is `name:tag@sha256:<digest>`. Images built
    from this repo (`build:`) pin their base in the Dockerfile instead; an override entry that
    only extends a base service names no image at all.
    """
    image = service["image"]

    assert DIGEST.search(image), f"{name}: image {image!r} is not pinned by digest"
    assert ":" in image.split("@")[0].rsplit("/", 1)[-1], f"{name}: image has no tag"


@DOCKERFILES
def test_dockerfile_bases_are_pinned_by_digest(path: Path) -> None:
    """SECURITY.md section 2: the images built from this repo pin their `FROM` base by digest."""
    text = path.read_text()

    bases = re.findall(r"^ARG \w+=(\S+)|^FROM (?!\$\{)(\S+)|^COPY --from=(\S+)", text, re.M)
    # A `COPY --from=<stage>` names a build stage, not an image; only registry refs have a `:`.
    refs = [ref for group in bases for ref in group if ref and ":" in ref]

    assert refs, f"{path.name}: no base image found"
    assert [ref for ref in refs if not DIGEST.search(ref)] == [], f"{path.name}: not by digest"


@DOCKERFILES
def test_dockerfile_installs_from_the_lock_with_hash_checking(path: Path) -> None:
    """SECURITY.md section 2: the image installs from `uv.lock` with `uv sync --locked`."""
    text = path.read_text()

    assert "uv sync --locked --no-dev" in text, f"{path.name}: does not install from the lock"


@DOCKERFILES
def test_dockerfile_runs_as_a_named_user_not_root(path: Path) -> None:
    """SECURITY.md section 2: the image switches to a named `USER`."""
    text = path.read_text()

    assert re.search(r"^USER \w+", text, re.M), f"{path.name}: runs as root"


@pytest.mark.parametrize(("path", "name", "service"), EVERY_SERVICE)
def test_published_ports_bind_loopback_only(path: Path, name: str, service: dict[str, Any]) -> None:
    """SECURITY.md section 3: only `127.0.0.1:8080` and `127.0.0.1:4000` are published."""
    ports = service.get("ports", [])

    assert [p for p in ports if not isinstance(p, str)] == [], f"{name}: write ports as strings"
    assert [p for p in ports if not PUBLISHED.match(p)] == [], f"{name}: port not allowed"


@pytest.mark.parametrize(("path", "name", "service"), EVERY_SERVICE)
def test_no_host_network_or_privilege(path: Path, name: str, service: dict[str, Any]) -> None:
    """SECURITY.md section 3: no `network_mode: host`, no `privileged`, no `extra_hosts`,
    no Docker socket mount.
    """
    volumes = [str(v) for v in service.get("volumes", [])]

    assert service.get("network_mode") != "host", f"{name}: network_mode host"
    assert not service.get("privileged"), f"{name}: privileged"
    assert "extra_hosts" not in service, f"{name}: extra_hosts"
    assert [v for v in volumes if "docker.sock" in v] == [], f"{name}: mounts the Docker socket"


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


@pytest.mark.parametrize(("path", "name", "service"), EVERY_SERVICE)
def test_service_reads_no_env_file_when_composed(
    path: Path, name: str, service: dict[str, Any]
) -> None:
    """SECURITY.md section 1: nobody has `env_file: .env`; it hands every key to the service."""
    assert "env_file" not in service, f"{name}: env_file hands every key to the service"


@pytest.mark.parametrize("path", [COMPOSE, COMPOSE_LOCAL], ids=lambda p: p.name)
def test_chassis_holds_litellm_api_key_and_no_master_or_provider_key_when_composed(
    path: Path,
) -> None:
    """SECURITY.md section 1: the chassis in each Compose file gets `LITELLM_API_KEY` only."""
    names = _env_names(_services(path)["chassis"])

    assert "LITELLM_API_KEY" in names
    assert not names & {"LITELLM_MASTER_KEY", "OPENAI_API_KEY"}, f"chassis: {names}"


def test_chassis_gets_the_router_url_from_the_base_compose_file() -> None:
    """SECURITY.md section 1: the base file sets `LITELLM_BASE_URL`; the override merges over it."""
    names = _env_names(_services(COMPOSE)["chassis"])

    assert "LITELLM_BASE_URL" in names


@pytest.mark.parametrize(("path", "name", "service"), NOT_CHASSIS_OR_ROUTER)
def test_service_holds_no_key_when_it_is_neither_chassis_nor_router(
    path: Path, name: str, service: dict[str, Any]
) -> None:
    """SECURITY.md section 1: the provider key and the master key go to LiteLLM only."""
    names = _env_names(service)

    assert {n for n in names if n.endswith("_KEY")} == set(), f"{name}: {names}"


@pytest.mark.parametrize("path", CHECKED_FILES, ids=lambda p: str(p.relative_to(COMPOSE_DIR)))
def test_no_value_looks_like_a_key(path: Path) -> None:
    """SECURITY.md section 1: a literal that starts with `sk-` is a defect."""
    text = DIGEST.sub("@sha256:<digest>", path.read_text())

    hits = [m.group(0) for m in KEY_LIKE.finditer(text)]

    assert hits == [], f"{path.relative_to(ROOT)}: key-like values {hits}"


@pytest.mark.parametrize("path", CHECKED_FILES, ids=lambda p: str(p.relative_to(COMPOSE_DIR)))
def test_api_key_literal_is_only_the_fake_placeholder(path: Path) -> None:
    """SECURITY.md section 1: `api_key: fake` is the one allowed literal; others read the env."""
    lines = [line.strip() for line in path.read_text().splitlines()]

    literals = [ln for ln in lines if "api_key" in ln and "os.environ/" not in ln]

    assert [ln for ln in literals if not ln.endswith("api_key: fake")] == [], path.name


def _env_example() -> list[tuple[str, str]]:
    lines = ENV_EXAMPLE.read_text().splitlines()
    return [line.partition("=")[::2] for line in lines if line and not line.startswith("#")]


def test_env_example_has_every_variable_empty() -> None:
    """SECURITY.md section 1: `.env.example` is committed with every name and an empty value."""
    filled = [name for name, value in _env_example() if value != ""]

    assert filled == [], f".env.example: {filled} are not empty"


def test_env_example_names_the_router_and_provider_keys() -> None:
    """SECURITY.md section 1: `.env.example` names the master, router, and provider keys."""
    names = {name for name, _ in _env_example()}

    assert {"LITELLM_MASTER_KEY", "LITELLM_API_KEY", "OPENAI_API_KEY"} <= names, names


def test_env_file_is_not_tracked_by_git() -> None:
    """SECURITY.md section 1: `deploy/compose/.env` is never committed."""
    listed = subprocess.run(
        ["git", "ls-files", "deploy/compose"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()

    assert "deploy/compose/.env" not in listed, ".env is tracked"


def test_local_config_switches_model_adapter_to_litellm() -> None:
    """Exit criterion: switching the model adapter (`fake` or `litellm`) or the model route needs
    a config change only. The Compose stack mounts this file as the chassis config.
    """
    config = _load(ROOT / "packages/chassis/configs/local.yaml")
    routes = {
        name: {m["model_name"] for m in _load(COMPOSE_DIR / name)["model_list"]}
        for name in ("litellm/config.yaml", "litellm/config.local.yaml")
    }

    assert config["profile"] == "local"
    assert config["spec"]["adapters"]["model"] == "litellm"
    assert config["spec"]["model"]["route"] == "big-default"
    assert "engine" not in config["spec"]["adapters"], "the lane is spec.engine.connector"
    assert config["spec"]["engine"]["connector"] == "inprocess", "no sidecar service in Compose yet"
    both = {"big-default", "local-small"}
    assert routes == {"litellm/config.yaml": both, "litellm/config.local.yaml": both}, routes


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


def wait_until_ready[T](
    probe: Callable[[], T | None], timeout_s: float, interval_s: float = 1.0
) -> T | None:
    """Call `probe` until it returns a value or the deadline passes; return the last value."""
    deadline = time.monotonic() + timeout_s
    value = probe()
    while value is None and time.monotonic() < deadline:
        time.sleep(interval_s)
        value = probe()
    return value


@pytest.fixture(scope="module")
def stack(request: pytest.FixtureRequest) -> Iterator[None]:
    """The Compose stack, up with `--wait` (health-gated), down with its volumes at the end."""
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run without --disable-socket to cover Compose")
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    _compose("up", "-d", "--wait", "--quiet-pull")
    try:
        yield
    finally:
        _compose("down", "-v", check=False)


@pytest.fixture(scope="module")
def complete_answer(stack: None) -> dict[str, Any]:
    """One complete `/v1/run`: chassis -> inprocess A2A -> echo_python -> chassis model proxy ->
    LiteLLM -> fake model server, whose `simplify` rule answers with 42 + 9 tokens.
    """
    answer: dict[str, Any] = httpx.post(f"{CHASSIS_URL}/v1/run", json=RUN_BODY, timeout=30.0).json()
    return answer


@pytest.fixture(scope="module")
def streamed_run(stack: None) -> tuple[int, list[tuple[str, dict[str, Any]]]]:
    """The same `/v1/run` streamed: the status code and the SSE frames."""
    with httpx.stream(
        "POST", f"{CHASSIS_URL}/v1/run", json={**RUN_BODY, "stream": True}, timeout=30.0
    ) as streamed:
        return streamed.status_code, _sse_frames(streamed.iter_lines())


@pytest.fixture(scope="module")
def router_logs(
    complete_answer: dict[str, Any], streamed_run: tuple[int, list[Any]]
) -> tuple[str, str | None]:
    """The router's logs once its `token_log` line is there, and that line (`None` if never)."""

    def token_line() -> tuple[str, str] | None:
        logs = _compose("logs", "--no-color", "litellm", check=False).stdout
        line = next((ln for ln in logs.splitlines() if "token_log status=ok" in ln), None)
        return None if line is None else (logs, line)

    found = wait_until_ready(token_line, LOG_TIMEOUT_S)
    if found is None:
        return _compose("logs", "--no-color", "litellm", check=False).stdout, None
    return found


@pytest.mark.network
@pytest.mark.slow
def test_compose_up_starts_the_chassis_and_the_router_with_no_manual_steps(stack: None) -> None:
    """Exit criterion: `docker compose up` starts the chassis and the router with no manual steps.
    Needs Docker and sockets, so it is out of `make test`.
    """
    health = httpx.get(f"{CHASSIS_URL}/health", timeout=5.0)

    assert health.json() == {"status": "ok"}


@pytest.mark.network
@pytest.mark.slow
def test_compose_run_answers_with_the_simplified_text_and_its_versions(
    complete_answer: dict[str, Any],
) -> None:
    """Exit criterion: `docker compose up` starts the chassis and the router with no manual steps;
    one request goes through the whole chain and reports the `versions` used.
    """
    answer = complete_answer

    assert answer["status"] == "ok", answer
    assert answer["output"]["text"] == SIMPLIFIED, answer
    assert answer["versions"]["model_route"] == "big-default", answer["versions"]
    assert answer["versions"]["prompt"] == "simplifier-v1", answer["versions"]


@pytest.mark.network
@pytest.mark.slow
def test_compose_run_reports_the_token_counts_of_the_request(
    complete_answer: dict[str, Any],
) -> None:
    """Exit criterion: token counts per request are visible; the response carries 42 + 9."""
    metrics = complete_answer["metrics"]

    assert metrics["input_tokens"] == 42, metrics
    assert metrics["output_tokens"] == 9, metrics


@pytest.mark.network
@pytest.mark.slow
def test_compose_stream_and_complete_carry_the_same_output(
    complete_answer: dict[str, Any], streamed_run: tuple[int, list[tuple[str, dict[str, Any]]]]
) -> None:
    """Exit criterion: streaming and complete responses carry the same output for the same input."""
    status, frames = streamed_run

    deltas = "".join(data["text"] for name, data in frames if name == "delta")
    final = next(data for name, data in frames if name == "response")

    assert status == 200
    assert deltas == complete_answer["output"]["text"], frames
    assert final["output"]["text"] == complete_answer["output"]["text"], final


@pytest.mark.network
@pytest.mark.slow
def test_router_logs_the_token_counts_of_each_request_tagged_with_the_agent(
    router_logs: tuple[str, str | None],
) -> None:
    """Exit criterion: token counts per request are visible in the router."""
    _, line = router_logs

    assert line, "the router did not log the token counts"
    assert "route=big-default" in line and "total_tokens=51" in line, line
    assert "tags=agent:echo" in line, line


@pytest.mark.network
@pytest.mark.slow
def test_router_logs_no_prompt_text_when_it_counts_tokens(
    router_logs: tuple[str, str | None],
) -> None:
    """SECURITY.md section 1: the router logs counts, never the prompt."""
    logs, _ = router_logs

    assert "quick brown fox" not in logs, "the router logged the prompt text"


@pytest.mark.network
@pytest.mark.slow
@pytest.mark.parametrize(
    ("service", "forbidden"),
    [("chassis", "OPENAI"), ("fake-model-server", "LITELLM")],
    ids=["chassis-sees-no-provider-key", "model-server-sees-no-router-key"],
)
def test_running_service_sees_no_key_meant_for_another(
    stack: None, service: str, forbidden: str
) -> None:
    """SECURITY.md section 1: inside the running stack, each key reaches one service only."""
    env = _compose("exec", "-T", service, "env").stdout

    assert forbidden not in env, f"{service} sees {forbidden}: {json.dumps(env)}"
