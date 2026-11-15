"""The dev hot reload (016 H-9): the Compose `dev` profile runs the workload's source live.

Only `dev` mounts `src/` and passes `--reload`. The `fake` and `local` profiles, the Dockerfile,
and the Helm chart stay as they were. The reloader (watchfiles) comes in the debug volume, pinned
by hash to the platform's lock, never in the image.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from agentctl.scaffold import Answers, render

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "my_agent"
SITE = "/app/.venv/lib/python3.12/site-packages"
"""Where the root Dockerfile's `uv sync --no-editable` installs the agent's package."""
LIVE = f"{SITE}/{PACKAGE}"
SOURCE_MOUNT = f"../../src/{PACKAGE}:{LIVE}:ro"
DEFAULT_PROFILES = ("fake", "local")
RELOAD_PATH = "/opt/debugpy/reload"
"""watchfiles's folder in the debug volume; only workload-dev has it on PYTHONPATH."""


@pytest.fixture(scope="module")
def agent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework="python",
        connector="sidecar",
        platform_path=ROOT,
    )
    return render(answers, tmp_path_factory.mktemp("dev-reload"))


@pytest.fixture(scope="module")
def compose(agent: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (agent / "deploy/compose/docker-compose.yaml").read_text()
    )
    return data


def _in_profile(compose: dict[str, Any], profile: str) -> dict[str, dict[str, Any]]:
    return {
        name: svc
        for name, svc in compose["services"].items()
        if profile in svc.get("profiles", [profile])
    }


def _locked_version(name: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    [version] = [p["version"] for p in lock["package"] if p["name"] == name]
    return str(version)


def test_the_dev_profile_mounts_the_source_read_only_and_runs_the_reloader(
    agent: Path, compose: dict[str, Any]
) -> None:
    assert (agent / "src" / PACKAGE / "handle.py").is_file()
    active = _in_profile(compose, "dev")
    assert {"debugpy", "fake-model-server", "chassis-fake", "workload-dev"} <= set(active)
    workload = active["workload-dev"]
    assert workload["profiles"] == ["dev"]
    assert workload["network_mode"] == "service:chassis-fake"
    assert SOURCE_MOUNT in workload["volumes"]
    assert workload["command"] == ["--reload", "--reload-dir", LIVE]
    assert "ports" not in workload and "restart" not in workload
    assert workload["read_only"] is True and workload["user"] == "10002:10002"
    # A literal empty value: an exported WORKLOAD_DEBUGPY_LISTEN must not reach the reloader.
    assert workload["environment"]["DEBUGPY_LISTEN"] == ""
    assert workload["environment"]["PYTHONPATH"] == f"/opt/debugpy:{RELOAD_PATH}"


def test_a_workload_change_never_touches_the_chassis(compose: dict[str, Any]) -> None:
    """The chassis gets no source, no reload, and no watch: only the workload's child restarts."""
    chassis = compose["services"]["chassis-fake"]
    assert "dev" in chassis["profiles"]
    assert not any("src" in str(v) for v in chassis["volumes"])
    assert "--reload" not in chassis["command"]
    sidecars = [
        name
        for name, svc in _in_profile(compose, "dev").items()
        if str(svc.get("network_mode", "")).startswith("service:")
    ]
    assert sidecars == ["workload-dev"], "one workload per chassis namespace"


@pytest.mark.parametrize("profile", DEFAULT_PROFILES)
def test_the_default_profiles_never_mount_the_source_or_reload(
    compose: dict[str, Any], profile: str
) -> None:
    for name, svc in _in_profile(compose, profile).items():
        assert "dev" not in svc.get("profiles", []) or name in {
            "debugpy",
            "fake-model-server",
            "chassis-fake",
        }, name
        assert not any("src" in str(v) for v in svc.get("volumes", [])), name
        assert "--reload" not in svc.get("command", []), name
        pythonpath = str(svc.get("environment", {}).get("PYTHONPATH", ""))
        assert RELOAD_PATH not in pythonpath, f"{name} can import watchfiles"
    assert "command" not in compose["services"][f"workload-{profile}"]


def test_the_production_image_and_chart_never_reload(agent: Path) -> None:
    dockerfile = (agent / "Dockerfile").read_text()
    assert "UV_PROJECT_ENVIRONMENT=/app/.venv" in dockerfile, "the dev mount's target"
    assert "python:3.12-slim@" in dockerfile, "the dev mount names python3.12"
    assert "--no-editable" in dockerfile
    chart = [p.read_text() for p in (agent / "deploy/helm").rglob("*") if p.is_file()]
    for text in (dockerfile, *chart, (agent / "pyproject.toml").read_text()):
        assert not re.search(r"--reload|watchfiles", text)


def test_the_dev_volume_pins_watchfiles_to_the_platform_lock(
    agent: Path, compose: dict[str, Any]
) -> None:
    debug = agent / "deploy/compose/debug"
    assert "watchfiles" not in (debug / "requirements.txt").read_text(), "not on debugpy's path"
    requirements = (debug / "reload-requirements.txt").read_text()
    version = _locked_version("watchfiles")
    assert f"watchfiles=={version} \\" in requirements
    block = requirements.split(f"watchfiles=={version}", 1)[1]
    hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", block)
    assert hashes, "watchfiles is pinned by hash"
    lock = (ROOT / "uv.lock").read_text()
    assert all(h in lock for h in hashes), "every watchfiles hash is one the platform locked"
    # watchfiles needs anyio, which the workload's own venv has; pip must not fetch it.
    dockerfile = (debug / "debugpy.Dockerfile").read_text()
    assert f"--no-deps --target {RELOAD_PATH} -r /tmp/reload-requirements.txt" in dockerfile
    # A new tool set gets a fresh volume: Docker fills a named volume only while it is empty.
    assert f"watchfiles-{version}" in compose["volumes"]["debugpy"]["name"]
    assert f"watchfiles-{version}" in compose["services"]["debugpy"]["image"]
