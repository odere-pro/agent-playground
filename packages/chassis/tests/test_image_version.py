"""One version for the package and the image, and `chassis serve` starts (H-1, issue 007).

The image is built from the repo root, from the same tree as the package: it copies the chassis
`pyproject.toml` and `src`, and `uv sync --locked` installs that package. So the image has no
version of its own. The real build is the `images` CI job (`.github/workflows/images.yml`); these
tests check the files and start `chassis serve` in process, offline.

`chassis.server` is imported here, at collection, as every other server test does: that import
(pydantic models of FastMCP, MCP, and Anthropic) is the slow part, and pytest pays it once.
"""

from __future__ import annotations

import asyncio
import re
import tomllib
from importlib.metadata import entry_points, version
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis import CHASSIS_VERSION, __version__, entry
from chassis.server.lifecycle import QuietServer

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
DOCKERFILE = (PACKAGE / "Dockerfile").read_text()
FAKE_YAML = str(PACKAGE / "configs/fake.yaml")
READY_TIMEOUT_S = 5.0


def project() -> dict[str, Any]:
    with (PACKAGE / "pyproject.toml").open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)["project"]
    return data


def test_the_package_the_module_and_the_install_report_one_version() -> None:
    declared = project()["version"]
    assert __version__ == CHASSIS_VERSION == declared
    assert version("chassis") == declared, "the installed package is the workspace one"


def test_the_image_installs_the_package_from_the_same_tree() -> None:
    assert "COPY packages/chassis/pyproject.toml packages/chassis/" in DOCKERFILE
    assert "COPY packages/chassis/src packages/chassis/src" in DOCKERFILE
    assert re.search(r"uv sync --locked --no-dev --package chassis\b", DOCKERFILE)
    assert not re.search(r"(?im)^\s*(ARG|ENV|LABEL)\b.*\bversion\b", DOCKERFILE), (
        "the image takes its version from the package, never from its own value"
    )


def test_the_images_job_builds_the_chassis_image_from_the_repo_root() -> None:
    workflow = (REPO / ".github/workflows/images.yml").read_text()
    assert re.search(r"^\s*chassis packages/chassis/Dockerfile \. 10001$", workflow, re.M)


def test_the_image_entry_point_is_the_chassis_console_script() -> None:
    assert 'ENTRYPOINT ["chassis", "serve"]' in DOCKERFILE
    assert project()["scripts"] == {"chassis": "chassis.entry:main"}
    (script,) = entry_points(group="console_scripts", name="chassis")
    assert script.value == "chassis.entry:main"


async def _ready_status(app: Any) -> int:
    """Run the app's lifespan and poll `/ready` until 200 or the timeout."""
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
    ):
        status = 0
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_S
        while asyncio.get_running_loop().time() < deadline:
            status = (await client.get("/ready")).status_code
            if status == 200:
                break
            await asyncio.sleep(0.02)
        return status


def test_chassis_serve_starts_and_answers_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """`chassis serve` from the console-script entry builds every server; the public app's
    lifespan runs and `/ready` answers 200. No socket: every server is a `QuietServer`, and
    its `serve` (the bind) is replaced in process.
    """
    ready: list[int] = []

    async def serve_in_process(self: QuietServer, sockets: Any = None) -> None:
        if self.config.lifespan == "on":  # the public server; the proxies have no lifespan
            ready.append(await _ready_status(self.config.app))
        self.started = True

    monkeypatch.setattr(QuietServer, "serve", serve_in_process)
    entry.main(["serve", "--config", FAKE_YAML, "--drain-delay-s", "0"])
    assert ready == [200]
