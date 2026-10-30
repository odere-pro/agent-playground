"""PoC-1 walking skeleton: `chassis serve` and `/v1/run` through the HTTP surface.

Each test names the exit criterion in docs/planning/poc/001-PoC-1-walking-skeleton.md it covers.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.core.envelope import Response
from chassis.server import create_app, load_config
from poc_harness.sockets import free_port
from poc_harness.sse import sse_frames

ROOT = Path(__file__).resolve().parents[3]
FAKE_CONFIG = ROOT / "packages/chassis/configs/fake.yaml"
CHASSIS_SRC = ROOT / "packages/chassis/src/chassis"
READY_TIMEOUT_S = 15.0


@pytest.fixture
def app_with_workload_routed_back(route_workload_to: Callable[[Any], Any]) -> Any:
    """The fake profile's app, with the workload's model call routed to the chassis's proxy app
    over an ASGI transport (the workload's test-only hook), so it reaches the fake model. The
    proxy app shares the public app's state; the public port has no model route (PoC-2 split).
    """
    return route_workload_to(create_app(load_config(FAKE_CONFIG)))


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
    frames = sse_frames(streamed.text)
    assert frames[0][0] == "start" and frames[-1][0] == "response"
    text = "".join(f[1]["text"] for f in frames if f[0] == "delta")
    final = Response.model_validate(frames[-1][1])
    assert text == complete.output["text"] == final.output["text"] == "ok"
    assert complete.status == final.status == "ok"
    assert complete.versions.config and complete.versions.model_route == "fake-route"
    assert complete.versions.prompt == "simplifier-v1"
    assert final.versions == complete.versions


def wait_until_ready(probe: Callable[[], bool], timeout_s: float, interval_s: float = 0.2) -> bool:
    """Call `probe` until it returns true or the deadline passes; return its last answer."""
    deadline = time.monotonic() + timeout_s
    ready = probe()
    while not ready and time.monotonic() < deadline:
        time.sleep(interval_s)
        ready = probe()
    return ready


def _is_ready(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/ready", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.network
@pytest.mark.slow
def test_chassis_serve_subprocess_answers_health(request: pytest.FixtureRequest) -> None:
    """Exit criterion: `chassis serve` starts the chassis from config. Runs the real console
    script on two free localhost ports, the public one and the model proxy's; the model route
    answers on the proxy port only. Needs a socket, so it stays out of `make test`.
    """
    if request.config.getoption("--disable-socket", default=False):
        pytest.skip("sockets are disabled; run without --disable-socket to cover the launcher")
    port = free_port()
    proxy_port = free_port()
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
            "--proxy-port",
            str(proxy_port),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        if not wait_until_ready(lambda: _is_ready(port), READY_TIMEOUT_S):
            proc.kill()
            out = proc.communicate(timeout=5)[0]
            pytest.fail(f"chassis serve did not become ready in {READY_TIMEOUT_S}s\n{out}")
        health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        assert health.json() == {"status": "ok"}
        chat = {"model": "fake-route", "messages": [{"role": "user", "content": "hi"}]}
        public = httpx.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=chat)
        assert public.status_code == 404
        proxied = httpx.post(f"http://127.0.0.1:{proxy_port}/v1/chat/completions", json=chat)
        assert proxied.status_code == 200, proxied.text
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_versions_prompt_matches_the_workload(app_with_workload_routed_back: Any) -> None:
    """Scope: the response reports the `versions` used. `versions.prompt` comes from config, so
    the config must name the prompt version the workload ships, or the response lies.
    """
    workload: Any = importlib.import_module("echo_python.handle")
    assert load_config(FAKE_CONFIG).spec.prompt.version == workload.PROMPT_VERSION
    app = app_with_workload_routed_back
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        out = (await client.post("/v1/run", json={"input": {"text": "simplify: x"}})).json()
    assert out["versions"]["prompt"] == workload.PROMPT_VERSION
