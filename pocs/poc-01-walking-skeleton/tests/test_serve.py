"""PoC-1 walking skeleton: `chassis serve` and `/v1/run` through the HTTP surface.

Each test names the exit criterion in docs/planning/poc/001-PoC-1-walking-skeleton.md it covers.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Response
from chassis.server import create_app, load_config

ROOT = Path(__file__).resolve().parents[3]
FAKE_CONFIG = ROOT / "packages/chassis/configs/fake.yaml"
CHASSIS_SRC = ROOT / "packages/chassis/src/chassis"


@pytest.fixture
def app_with_workload_routed_back() -> Iterator[Any]:
    """The fake profile's app, with the workload's model call routed to the app itself over an
    ASGI transport (the workload's test-only hook), so it reaches the proxy and the fake model.
    """
    app = create_app(load_config(FAKE_CONFIG))
    workload: Any = importlib.import_module("echo_python.handle")
    workload.transport = httpx.ASGITransport(app=app)
    try:
        yield app
    finally:
        workload.transport = None


async def test_chassis_serve_starts_from_config() -> None:
    """Exit criterion: `chassis serve` starts the chassis from config. The same `create_app` and
    `load_config` the launcher calls bring `/ready` to 200 with the fake profile and no network.
    """
    config = load_config(FAKE_CONFIG)
    assert config.profile == "fake" and config.agent.name == "echo"
    assert config.version, "the config version is the file's content hash"
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://chassis") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/ready")).status_code == 503
        async with app.router.lifespan_context(app):
            assert (await client.get("/ready")).status_code == 200


def test_chassis_package_holds_no_business_logic() -> None:
    """Exit criterion: the `chassis` package holds no business logic. An import-linter contract
    forbids `chassis` from importing `echo_python`, direct or indirect (`test_day0.py::
    test_import_rules_hold` runs it; the connector loads the handle by dotted path at runtime,
    which import-linter does not see). A grep over `server` and `core` is the second check.
    """
    with (ROOT / "pyproject.toml").open("rb") as fh:
        contracts = tomllib.load(fh)["tool"]["importlinter"]["contracts"]
    rules = [
        c
        for c in contracts
        if c["type"] == "forbidden"
        and "chassis" in c["source_modules"]
        and "echo_python" in c["forbidden_modules"]
    ]
    assert rules, "no import-linter contract forbids chassis -> echo_python"
    assert all("allow_indirect_imports" not in c for c in rules), "indirect imports must count"

    offenders: list[str] = []
    for folder in ("server", "core"):
        for path in (CHASSIS_SRC / folder).rglob("*.py"):
            for line in path.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith(("import ", "from ")) and (
                    "workloads" in stripped or "echo_python" in stripped
                ):
                    offenders.append(f"{path.relative_to(ROOT)}: {stripped}")
    assert offenders == []


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if block.strip():
            event, data = block.splitlines()[:2]
            out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def test_stream_and_complete_carry_the_same_output(
    app_with_workload_routed_back: Any,
) -> None:
    """Exit criterion: streaming and complete responses carry the same output for the same input.
    Also: the response reports the `versions` used (config, prompt, model route). The fake
    profile runs the simplifier in the `inprocess` lane; the scripted model answers "ok".
    """
    app = app_with_workload_routed_back
    body = {"input": {"text": "simplify: the quick brown fox"}}
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        complete = Response.model_validate((await client.post("/v1/run", json=body)).json())
        streamed = await client.post("/v1/run", json={**body, "stream": True})
    frames = _frames(streamed.text)
    assert frames[0][0] == "start" and frames[-1][0] == "response"
    text = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    final = Response.model_validate(frames[-1][1])
    assert text == complete.output["text"] == final.output["text"] == "ok"
    assert complete.status == final.status == "ok"
    assert complete.versions.config and complete.versions.model_route == "fake-route"
    assert complete.versions.prompt == "simplifier-v1"
    assert final.versions == complete.versions


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.network
@pytest.mark.slow
def test_chassis_serve_subprocess_answers_health(request: pytest.FixtureRequest) -> None:
    """Exit criterion: `chassis serve` starts the chassis from config. Runs the real console
    script on a free localhost port; needs a socket, so it stays out of `make test`.
    """
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run without --disable-socket to cover the launcher")
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "chassis.server.cli",
            "serve",
            "--config",
            str(FAKE_CONFIG),
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                ready = httpx.get(f"http://127.0.0.1:{port}/ready", timeout=1.0)
                if ready.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last = exc
            time.sleep(0.2)
        else:
            proc.kill()
            out = proc.communicate(timeout=5)[0]
            pytest.fail(f"chassis serve did not become ready: {last!r}\n{out}")
        health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        assert health.json() == {"status": "ok"}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
