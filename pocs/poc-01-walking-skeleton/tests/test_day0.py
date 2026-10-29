"""PoC-1 day 0: the scenarios behind the exit criteria that day 0 already meets.

Each test names the exit criterion in docs/planning/poc/001-PoC-1-walking-skeleton.md it covers.
"""

from __future__ import annotations

import ast
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a import InProcessConnector
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.profiles import AdapterNotAllowed, AdapterNotAvailable, LaneNotAllowed, build_ports
from chassis.schemas import SCHEMA_DIR, drift
from chassis.server import create_app as create_chassis_app
from chassis.server import load_config
from chassis_contracts import (
    ConfigPortContract,
    EngineConnectorContract,
    ModelPortContract,
    TelemetryPortContract,
)
from fake_model_server import Script, create_app
from pytest_socket import SocketBlockedError

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"


CHASSIS_TESTS = ROOT / "packages/chassis/tests"


@pytest.fixture
def offline_gate(request: pytest.FixtureRequest) -> None:
    """Skip unless pytest runs the way `make test` runs it: `scripts/check_offline.sh`."""
    if not request.config.getoption("--disable-socket", default=False):
        pytest.skip("not under the offline gate; run `make test` or `make test-poc POC=01`")


def test_fake_profile_reaches_no_socket_when_the_offline_gate_runs(offline_gate: None) -> None:
    """Exit criterion: `make test` passes offline with the `fake` profile: no network."""
    build_ports("fake")

    with pytest.raises(SocketBlockedError):
        socket.create_connection(("127.0.0.1", 9), timeout=1.0)


def test_fake_profile_sees_no_api_key_when_the_offline_gate_runs(offline_gate: None) -> None:
    """Exit criterion: `make test` passes offline with the `fake` profile: no keys."""
    build_ports("fake")

    keys = [name for name in os.environ if name.endswith("_API_KEY")]

    assert keys == []


def test_fake_profile_builds_the_four_day0_ports() -> None:
    """Exit criterion: each of the four ports has a fake; the `fake` profile builds all four."""
    ports = build_ports("fake", connector="inprocess")

    assert isinstance(ports.model, ScriptedModel)
    assert isinstance(ports.engine, InProcessConnector)
    assert isinstance(ports.config, InMemoryConfig)
    assert isinstance(ports.telemetry, InMemoryTelemetry)


def _contract_bindings() -> dict[str, tuple[set[str], set[str]]]:
    """Every `Test*` class under packages/chassis/tests: its base names and the names it uses.

    Read with `ast`, not imported: the chassis tests folder is not a package, and pytest runs
    the bindings themselves in the same gate.
    """
    found: dict[str, tuple[set[str], set[str]]] = {}
    for path in sorted(CHASSIS_TESTS.rglob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                found[node.name] = (bases, used)
    return found


@pytest.mark.parametrize(
    ("binding", "suite", "fake"),
    [
        ("TestScriptedModel", ModelPortContract, ScriptedModel),
        ("TestFakeEngineHandle", EngineConnectorContract, FakeEngine),
        ("TestInMemoryConfig", ConfigPortContract, InMemoryConfig),
        ("TestInMemoryTelemetry", TelemetryPortContract, InMemoryTelemetry),
    ],
    ids=["model", "engine", "config", "telemetry"],
)
def test_each_day0_fake_is_bound_to_its_contract_suite_by_name(
    binding: str, suite: type, fake: type
) -> None:
    """Exit criterion: each of the four ports has an interface, a fake, and a contract suite that
    the fake passes. The named binding subclasses the suite and builds the fake; `make test` runs
    it in the same gate.
    """
    bindings = _contract_bindings()

    bases, used = bindings.get(binding, (set(), set()))

    assert suite.__name__ in bases, f"{binding} does not bind {suite.__name__}"
    assert fake.__name__ in used, f"{binding} does not build {fake.__name__}"


def _config(profile: str, engine: dict[str, Any], adapters: dict[str, Any] | None = None) -> Any:
    spec: dict[str, Any] = {"engine": engine, "trust": "trusted"}  # `cloud` must name it (PoC-5)
    if adapters is not None:
        spec["adapters"] = adapters
    return {"profile": profile, "agent": {"name": "echo", "version": "0.0.1"}, "spec": spec}


INPROCESS = {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_inprocess_lane_builds_when_the_profile_is_fake_or_local(profile: str) -> None:
    """Exit criterion (ADR-001 item 4): `inprocess` is for the chassis's tests and local runs.
    Through the loader and `build_ports`, the way `chassis serve` reaches them.
    """
    adapters = {
        "model": "fake",
        "config": "memory",
        "telemetry": "memory",
        "tools": "fake",
        "state": "memory",  # PoC-4: `local` defaults to `valkey`
    }
    config = load_config(_config(profile, INPROCESS, adapters))

    ports = build_ports(
        config.profile, config.spec.adapters, connector=config.spec.engine.connector
    )

    assert isinstance(ports.engine, InProcessConnector)


def test_inprocess_lane_is_refused_by_the_loader_when_the_profile_is_cloud() -> None:
    """Exit criterion (ADR-001 item 4): `inprocess` is refused outside `fake` and `local`."""
    config = _config("cloud", INPROCESS)

    with pytest.raises(ValueError, match="'inprocess' is for the chassis's own tests"):
        load_config(config)  # `LaneNotAllowed`, inside pydantic's error


def test_inprocess_lane_is_refused_by_build_ports_when_the_profile_is_cloud() -> None:
    """Exit criterion (ADR-001 item 4): `build_ports` refuses `inprocess` in `cloud` too."""
    with pytest.raises(LaneNotAllowed):
        build_ports("cloud", connector="inprocess")


async def test_the_review_probe_no_longer_runs_in_process_in_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract v1 decision 2, the PoC-1 review probe. `profile: cloud` with
    `engine.connector: sidecar` passes the loader, but an injected in-process engine is refused
    by the lifespan once the ports exist; `adapters.engine` cannot be set at all; and
    `adapters: {}` in `cloud` builds the `cloud` defaults, not fakes.
    """
    sidecar = {"connector": "sidecar", "url": "http://127.0.0.1:9000"}
    config = load_config(_config("cloud", sidecar))
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
    )
    app = create_chassis_app(config, bundle)
    with pytest.raises(LaneNotAllowed, match="'inprocess'"):
        async with app.router.lifespan_context(app):
            pass
    assert app.state.ready is False

    with pytest.raises(ValueError, match=r"spec\.engine\.connector"):
        load_config(_config("cloud", sidecar, {"engine": "inprocess"}))

    monkeypatch.setenv("LITELLM_BASE_URL", "http://router:4000/v1")
    monkeypatch.delenv("CONFIG_S3_ENDPOINT", raising=False)
    empty = load_config(_config("cloud", sidecar, {}))
    with pytest.raises(AdapterNotAvailable, match=r"arrives in PoC|is not configured"):
        build_ports(empty.profile, empty.spec.adapters, connector=empty.spec.engine.connector)
    with pytest.raises(AdapterNotAllowed, match="model: 'fake'"):
        build_ports(
            "cloud", load_config(_config("cloud", sidecar, {"model": "fake"})).spec.adapters
        )


def test_published_schemas_are_current_and_cover_the_contract() -> None:
    """Exit criterion: contract v0 is written down. The schemas are generated and checked in."""
    assert drift() == []
    names = {p.name for p in SCHEMA_DIR.glob("*.json")}
    expected = {"events", "task_input", "context", "request", "response"}
    assert {f"{n}.v0.json" for n in expected} <= names
    events = json.loads((SCHEMA_DIR / "events.v0.json").read_text())
    assert "oneOf" in events or "anyOf" in events, "the event schema is a discriminated union"


async def test_fake_model_server_streams_and_completes_the_same_text() -> None:
    """Exit criterion: streaming and complete responses carry the same output for the same input."""
    app = create_app(Script.from_yaml(EXAMPLE_SCRIPT))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        body = {"messages": [{"role": "user", "content": "simplify: the quick brown fox"}]}
        complete = (await client.post("/v1/chat/completions", json=body)).json()
        streamed = (await client.post("/v1/chat/completions", json={**body, "stream": True})).text
    chunks = [json.loads(line[6:]) for line in streamed.splitlines() if line.startswith("data: {")]
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    expected = "Plain words. Short sentences. Same facts."
    assert text == complete["choices"][0]["message"]["content"] == expected


@pytest.mark.slow
def test_import_rules_hold() -> None:
    """Day-0 scope: the import-lint rule. No product SDK outside its adapter, no framework.

    Runs the `lint-imports` console script next to this interpreter; `python -m importlinter.cli`
    has no `__main__` guard, exits 0, and checks nothing.
    """
    lint_imports = Path(sys.executable).with_name("lint-imports")
    assert lint_imports.exists(), f"no lint-imports next to {sys.executable}"
    result = subprocess.run(
        [str(lint_imports)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    counts = re.search(r"Contracts: (\d+) kept, (\d+) broken", output)
    assert counts is not None, output
    assert int(counts.group(1)) > 0 and counts.group(2) == "0", output
