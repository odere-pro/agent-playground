"""PoC-9 template ops: the Compose profiles, the debuggers, and the template's own CI.

Scope items covered (docs/planning/poc/009-PoC-9-agent-mvp-template.md):

- "Docker Compose profiles (`fake`, `local`) that run both containers, the chassis and the
  workload, with a debugger attached to each."
- "CI in the template: lint (including the rule that keeps frameworks out of the chassis), the
  lane contract suite over A2A on localhost and in memory, the load smoke test, the hostile and
  red-team suites, and the offline eval." and "suggested: image signing with cosign."

They feed the exit criterion "A scaffolded agent passes its own CI with no manual edits": these
tests check the files the scaffold renders. The run itself needs the rendered agent's Makefile
(agentctl) and a CI runner, so it is not here.

Each template file under `packages/agentctl/src/agentctl/template/project/` is rendered with
Jinja2 and sample answers, as Copier would, then read as data. Offline: no socket, no key.
`actionlint` and `docker compose config` run when they are on PATH (neither needs the network or
a daemon).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

jinja2 = pytest.importorskip("jinja2")

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "packages/agentctl/src/agentctl/template/project"
OWNED = (
    "deploy/compose/docker-compose.yaml.jinja",
    "deploy/compose/README.md.jinja",
    "deploy/compose/debug/debugpy.Dockerfile",
    "deploy/compose/debug/requirements.txt",
    "deploy/compose/debug/sitecustomize.py",
    ".vscode/launch.json.jinja",
    ".github/workflows/ci.yml.jinja",
    ".gitlab-ci.yml.jinja",
)
"""The template files this piece owns; the rest of the template is agentctl's."""

ANSWERS: dict[str, str] = {
    "agent_name": "acme-glossary",
    "package_name": "acme_glossary",
    "kind": "transformer",
    "framework": "pydanticai",
    "connector": "sidecar",
    "trust": "trusted",
    "platform_path": "../agent-platform",
}
PROFILES = ("fake", "local")
KEY_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
WORKLOAD_ENV = {
    "CHASSIS_MODEL_URL",
    "CHASSIS_TOOL_URL",
    "HOST",
    "PORT",
    "PYTHONPATH",
    "DEBUGPY_LISTEN",
    "DEBUGPY_WAIT",
}
"""The four platform variables (deploy/compose/SECURITY.md, 6) and the debugger's three."""
APP_PORT, CHASSIS_DEBUG, WORKLOAD_DEBUG, SIDECAR_PORT = 8080, 5678, 5679, 9000
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
CI_TARGETS = ("load-smoke", "hostile", "redteam", "eval")
"""The suite targets; with `lint`, `test`, `image`, and `sign` they are the agent's Makefile API."""


def render_tree(dest: Path, answers: dict[str, str]) -> Path:
    """Render the owned files into `dest` the way Copier does: `.jinja` is rendered and dropped."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    for rel in OWNED:
        src = TEMPLATE / rel
        if rel.endswith(".jinja"):
            out = dest / rel.removesuffix(".jinja")
            text = env.get_template(rel).render(**answers)
        else:
            out = dest / rel
            text = src.read_text()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return dest


@pytest.fixture(scope="module")
def agent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return render_tree(tmp_path_factory.mktemp("acme-glossary"), ANSWERS)


@pytest.fixture(scope="module")
def compose(agent: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (agent / "deploy/compose/docker-compose.yaml").read_text()
    )
    return data


@pytest.fixture(scope="module")
def workflow(agent: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((agent / ".github/workflows/ci.yml").read_text())
    return data


@pytest.fixture(scope="module")
def gitlab(agent: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((agent / ".gitlab-ci.yml").read_text())
    return data


def services_in(compose: dict[str, Any], profile: str) -> dict[str, dict[str, Any]]:
    return {
        name: svc
        for name, svc in compose["services"].items()
        if profile in svc.get("profiles", [profile])
    }


def published(svc: dict[str, Any]) -> list[tuple[str, str, int]]:
    """`host_ip:host_port:container_port` strings as tuples."""
    out = []
    for entry in svc.get("ports", []):
        # The host port may be an interpolation with its own colon: `${CHASSIS_PORT:-8080}`.
        host_ip, rest = str(entry).split(":", 1)
        host_port, container = rest.rsplit(":", 1)
        out.append((host_ip, host_port, int(container)))
    return out


def env_default(value: str) -> str:
    """The default of a Compose `${NAME-default}` or `${NAME:-default}` interpolation."""
    match = re.fullmatch(r"\$\{\w+:?-(.*)\}", value)
    assert match, f"not an interpolation with a default: {value!r}"
    return match.group(1)


# --- rendering ------------------------------------------------------------------------------------


def test_every_owned_file_exists_and_renders_without_leftovers(agent: Path) -> None:
    for rel in OWNED:
        assert (TEMPLATE / rel).is_file(), rel
    for path in agent.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text()
        if path.name == "ci.yml":
            # GitHub's own `${{ ... }}` expressions survive the render on purpose ({% raw %}).
            assert "${{ github.ref }}" in text
            text = re.sub(r"\$\{\{.*?\}\}", "", text)
        # A Jinja leftover starts with a name; Go templates (`{{.Config.User}}`) are meant.
        assert not re.search(r"\{\{-?\s*[A-Za-z_]", text), path
        assert "{%" not in text, path
    assert "acme-glossary" in (agent / "deploy/compose/docker-compose.yaml").read_text()


def test_compose_builds_from_the_platform_link_not_a_path_of_this_machine(tmp_path: Path) -> None:
    """The Compose file works from any clone: it names `.platform`, which `make` links."""
    agent = render_tree(tmp_path / "agent", {**ANSWERS, "platform_path": "/opt/agent-platform"})
    compose = yaml.safe_load((agent / "deploy/compose/docker-compose.yaml").read_text())
    assert compose["services"]["fake-model-server"]["build"]["context"] == "../../.platform"
    assert "/opt/agent-platform" not in (agent / "deploy/compose/docker-compose.yaml").read_text()


# --- Compose: profiles, keys, ports, debuggers ----------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_each_profile_runs_the_chassis_and_the_workload_as_sidecar(
    compose: dict[str, Any], profile: str
) -> None:
    active = services_in(compose, profile)
    chassis, workload = f"chassis-{profile}", f"workload-{profile}"
    assert {chassis, workload, "debugpy"} <= set(active)
    assert active[workload]["network_mode"] == f"service:{chassis}"
    assert active[workload]["build"]["context"] == "../.."
    assert active[workload]["build"]["additional_contexts"] == {
        "workload-a2a": "../../.platform/packages/workload-a2a"
    }
    assert active[chassis]["image"].startswith("${CHASSIS_IMAGE:-")
    assert "build" not in active[chassis], "the chassis image is pinned, never built here"
    sidecars = [
        n for n, s in active.items() if str(s.get("network_mode", "")).startswith("service:")
    ]
    assert sidecars == [workload]


def test_profiles_mount_their_own_chassis_config(compose: dict[str, Any]) -> None:
    mounts = {p: compose["services"][f"chassis-{p}"]["volumes"] for p in PROFILES}
    assert "../../config/fake.yaml:/etc/chassis/config.yaml:ro" in mounts["fake"]
    assert "../../config/agent.yaml:/etc/chassis/config.yaml:ro" in mounts["local"]
    assert "fake-model-server" in compose["services"]["chassis-fake"]["depends_on"]
    assert services_in(compose, "fake").keys() - services_in(compose, "local").keys() == {
        "fake-model-server",
        "chassis-fake",
        "workload-fake",
    }


def test_only_the_chassis_gets_a_key(compose: dict[str, Any]) -> None:
    keyed = {}
    for name, svc in compose["services"].items():
        assert "env_file" not in svc, name
        if "secrets" in svc:
            assert name.startswith("chassis-"), f"{name} mounts a secret"
        keys = {k for k in svc.get("environment", {}) if KEY_NAME.search(k)}
        if keys:
            keyed[name] = keys
    assert keyed == {"chassis-local": {"LITELLM_API_KEY"}}
    assert compose["services"]["chassis-local"]["environment"]["LITELLM_API_KEY"] == (
        "${LITELLM_API_KEY:-}"
    ), "the key comes from the environment, never a literal"
    assert "secrets" not in compose


@pytest.mark.parametrize("profile", PROFILES)
def test_workload_is_hardened_keyless_and_unpublished(
    compose: dict[str, Any], profile: str
) -> None:
    svc = compose["services"][f"workload-{profile}"]
    assert "ports" not in svc and "expose" not in svc
    assert set(svc["environment"]) == WORKLOAD_ENV
    assert svc["environment"]["HOST"] == "127.0.0.1"
    assert svc["environment"]["PORT"] == str(SIDECAR_PORT)
    assert svc["environment"]["CHASSIS_MODEL_URL"] == "http://127.0.0.1:8090/v1"
    assert svc["cap_drop"] == ["ALL"]
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["read_only"] is True
    assert svc["user"] == "10002:10002"
    assert compose["services"][f"chassis-{profile}"]["user"] == "10001:10001"
    assert "restart" not in svc and "restart" not in compose["services"][f"chassis-{profile}"]


def test_every_published_port_is_on_loopback_and_only_on_the_chassis(
    compose: dict[str, Any],
) -> None:
    for name, svc in compose["services"].items():
        ports = published(svc)
        assert all(ip == "127.0.0.1" for ip, _, _ in ports), name
        assert svc.get("network_mode") != "host", name
        if not name.startswith("chassis-"):
            assert not ports, f"{name} publishes {ports}"
            continue
        targets = [c for _, _, c in ports]
        assert sorted(targets) == [CHASSIS_DEBUG, WORKLOAD_DEBUG, APP_PORT], name
        assert SIDECAR_PORT not in targets and 8090 not in targets
        host_ports = [h for _, h, _ in ports]
        assert len(set(host_ports)) == len(host_ports), "debug ports must be distinct"


@pytest.mark.parametrize("profile", PROFILES)
def test_a_debugger_is_attached_to_each_container(compose: dict[str, Any], profile: str) -> None:
    chassis = compose["services"][f"chassis-{profile}"]
    workload = compose["services"][f"workload-{profile}"]
    for svc in (chassis, workload):
        assert "debugpy:/opt/debugpy:ro" in svc["volumes"]
        assert svc["environment"]["PYTHONPATH"] == "/opt/debugpy"
        assert env_default(svc["environment"]["DEBUGPY_WAIT"]) == "0", "wait-for-client is off"
    assert env_default(workload["environment"]["DEBUGPY_LISTEN"]) == f"auto:{WORKLOAD_DEBUG}"
    assert compose["services"]["debugpy"]["network_mode"] == "none"
    assert chassis["depends_on"]["debugpy"] == {"condition": "service_completed_successfully"}


def test_chassis_debugger_is_on_in_fake_and_opt_in_where_it_would_expose_the_key(
    compose: dict[str, Any],
) -> None:
    """The workload shares the chassis's namespace and debugpy has no auth: in `local` a listening
    chassis debugger would hand the workload LITELLM_API_KEY, so it is off unless asked for."""
    fake = compose["services"]["chassis-fake"]["environment"]["DEBUGPY_LISTEN"]
    local = compose["services"]["chassis-local"]["environment"]["DEBUGPY_LISTEN"]
    assert env_default(fake) == f"auto:{CHASSIS_DEBUG}"
    assert env_default(local) == ""


def test_debugpy_is_pinned_by_hash_and_versioned_everywhere(compose: dict[str, Any]) -> None:
    reqs = (TEMPLATE / "deploy/compose/debug/requirements.txt").read_text()
    version = re.search(r"^debugpy==(\S+) \\$", reqs, re.MULTILINE)
    assert version, "debugpy must be pinned exactly"
    assert len(re.findall(r"--hash=sha256:[0-9a-f]{64}", reqs)) >= 2
    assert compose["services"]["debugpy"]["image"].endswith(f":{version.group(1)}")
    assert compose["volumes"]["debugpy"]["name"].endswith(f"-debugpy-{version.group(1)}")
    dockerfile = (TEMPLATE / "deploy/compose/debug/debugpy.Dockerfile").read_text()
    assert "--require-hashes" in dockerfile
    assert re.search(r"python:3\.12-slim@sha256:[0-9a-f]{64}", dockerfile)


def test_vscode_attaches_to_the_published_debug_ports(agent: Path) -> None:
    launch = json.loads((agent / ".vscode/launch.json").read_text())
    by_port = {c["connect"]["port"]: c for c in launch["configurations"]}
    assert set(by_port) == {CHASSIS_DEBUG, WORKLOAD_DEBUG}
    for config in by_port.values():
        assert config["type"] == "debugpy" and config["request"] == "attach"
        assert config["connect"]["host"] == "127.0.0.1"
    chassis_map = by_port[CHASSIS_DEBUG]["pathMappings"][0]
    assert chassis_map["remoteRoot"] == "/app/packages/chassis/src"
    assert chassis_map["localRoot"] == "${workspaceFolder}/../agent-platform/packages/chassis/src"
    names = {c["name"] for c in launch["configurations"]}
    assert set(launch["compounds"][0]["configurations"]) == names


# --- debugpy hook ---------------------------------------------------------------------------------


@pytest.fixture
def hook(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.delenv("DEBUGPY_LISTEN", raising=False)
    path = TEMPLATE / "deploy/compose/debug/sitecustomize.py"
    spec = importlib.util.spec_from_file_location("poc09_template_sitecustomize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # its import-time start() is a no-op: no DEBUGPY_LISTEN
    monkeypatch.setattr(module.socket, "gethostbyname", lambda _name: "172.18.0.5")
    yield module


@pytest.fixture
def fake_debugpy(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls: list[Any] = []
    fake = SimpleNamespace(
        calls=calls,
        listen=lambda address: calls.append(("listen", address)),
        wait_for_client=lambda: calls.append(("wait",)),
    )
    monkeypatch.setitem(sys.modules, "debugpy", fake)
    return fake


def test_hook_listens_only_in_the_service_process(
    hook: ModuleType, fake_debugpy: SimpleNamespace
) -> None:
    env = {"DEBUGPY_LISTEN": "auto:5679"}
    assert hook.start(env, pid=42) is None, "a healthcheck or the adapter must not take the port"
    assert hook.start({}, pid=1) is None
    assert hook.start({"DEBUGPY_LISTEN": ""}, pid=1) is None
    assert fake_debugpy.calls == []
    assert hook.start(env, pid=1) == ("172.18.0.5", 5679)
    assert fake_debugpy.calls == [("listen", ("172.18.0.5", 5679))]


def test_hook_waits_only_when_asked(hook: ModuleType, fake_debugpy: SimpleNamespace) -> None:
    hook.start({"DEBUGPY_LISTEN": "0.0.0.0:5678", "DEBUGPY_WAIT": "1"}, pid=1)
    assert fake_debugpy.calls == [("listen", ("0.0.0.0", 5678)), ("wait",)]


def test_hook_failure_is_reported_and_never_stops_the_service(
    hook: ModuleType, fake_debugpy: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert hook.start({"DEBUGPY_LISTEN": "nonsense"}, pid=1) is None
    assert "debugpy: not started" in capsys.readouterr().err
    assert fake_debugpy.calls == []


# --- CI: GitHub Actions and GitLab ----------------------------------------------------------------


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = job["steps"]
    return out


def test_every_github_action_is_pinned_by_full_sha(workflow: dict[str, Any]) -> None:
    uses = [s["uses"] for job in workflow["jobs"].values() for s in steps(job) if "uses" in s]
    assert uses
    unpinned = [u for u in uses if not PINNED_ACTION.match(u)]
    assert unpinned == []


def test_github_ci_runs_every_stage_through_make(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    assert set(jobs) == {"lint", "test", "suites", "image", "sign"}
    runs = {name: "\n".join(s.get("run", "") for s in steps(job)) for name, job in jobs.items()}
    assert "make lint" in runs["lint"]
    assert "chassis" in runs["lint"], "the workload-side import rule has its own guard"
    assert "make test" in runs["test"]
    assert jobs["suites"]["strategy"]["matrix"]["target"] == list(CI_TARGETS)
    assert 'make "$TARGET"' in runs["suites"]
    assert "make image" in runs["image"] and "PUSH=1" in runs["image"]
    assert "make sign" in runs["sign"] and "cosign verify" in runs["sign"]
    assert set(jobs["image"]["needs"]) == {"lint", "test"}


def test_github_signing_is_keyless_and_default_branch_only(workflow: dict[str, Any]) -> None:
    raw: dict[Any, Any] = workflow
    triggers = raw.get("on") or raw[True]  # PyYAML reads a bare `on` as True
    assert "pull_request_target" not in triggers
    assert workflow["permissions"] == {"contents": "read"}
    default_branch = (
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
    )
    sign = workflow["jobs"]["sign"]
    assert default_branch in sign["if"] and "github.event_name == 'push'" in sign["if"]
    assert sign["permissions"]["id-token"] == "write"
    for name, job in workflow["jobs"].items():
        if name != "sign":
            assert "id-token" not in job.get("permissions", {}), name
    pushing = [s for s in steps(workflow["jobs"]["image"]) if "PUSH=1" in s.get("run", "")]
    logins = [s for s in steps(workflow["jobs"]["image"]) if "login-action" in s.get("uses", "")]
    assert pushing and logins
    for step in pushing + logins:
        assert default_branch in step["if"]
    for job in workflow["jobs"].values():
        for step in steps(job):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_gitlab_ci_has_the_same_stages_and_pins(gitlab: dict[str, Any]) -> None:
    assert gitlab["stages"] == ["lint", "test", "suites", "image", "sign"]
    script = {name: "\n".join(gitlab[name]["script"]) for name in gitlab["stages"]}
    assert "make lint" in script["lint"]
    assert "make test" in script["test"]
    assert gitlab["suites"]["parallel"]["matrix"] == [{"TARGET": list(CI_TARGETS)}]
    assert 'make "$TARGET"' in script["suites"]
    assert "make image" in script["image"] and "make sign" in script["sign"]
    for var in ("UV_IMAGE", "DOCKER_CLI_IMAGE", "DOCKER_DIND_IMAGE"):
        assert DIGEST.search(gitlab["variables"][var]), var
    sign = gitlab["sign"]
    assert sign["rules"] == [{"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"}]
    assert sign["id_tokens"]["SIGSTORE_ID_TOKEN"]["aud"] == "sigstore"
    assert "sha256sum -c" in "\n".join(sign["before_script"]), "cosign is checked by its sum"
    assert "$CI_DEFAULT_BRANCH" in script["image"], "only the default branch pushes"


def test_actionlint_accepts_the_rendered_workflow(agent: Path) -> None:
    actionlint = shutil.which("actionlint")
    if actionlint is None:
        pytest.skip("actionlint is not on PATH (uv sync installs actionlint-py)")
    result = subprocess.run(
        [actionlint, "-no-color", str(agent / ".github/workflows/ci.yml")],
        capture_output=True,
        text=True,
        check=False,
        cwd=agent,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("profile", PROFILES)
def test_docker_compose_config_accepts_each_profile(agent: Path, profile: str) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker is not installed")
    probe = subprocess.run(
        [docker, "compose", "version"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        pytest.skip("docker compose is not available")
    # `config` parses and interpolates only; it needs no daemon and no network.
    result = subprocess.run(
        [
            docker,
            "compose",
            "-f",
            str(agent / "deploy/compose/docker-compose.yaml"),
            "--profile",
            profile,
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert {f"chassis-{profile}", f"workload-{profile}", "debugpy"} <= set(rendered["services"])
    for name, svc in rendered["services"].items():
        for port in svc.get("ports", []):
            assert port["host_ip"] == "127.0.0.1", name
