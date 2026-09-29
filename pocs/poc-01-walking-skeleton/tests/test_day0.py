"""PoC-1 day 0: the scenarios behind the exit criteria that day 0 already meets.

Each test names the exit criterion in docs/planning/poc/001-PoC-1-walking-skeleton.md it covers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.profiles import LaneNotAllowed, build_ports, check_lane
from chassis.schemas import SCHEMA_DIR, drift
from fake_model_server import Script, create_app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"


def test_fake_profile_builds_the_four_day0_ports() -> None:
    """Exit criterion: every port has an interface, a fake, and a contract suite that the fake
    passes.
    """
    ports = build_ports("fake")
    assert isinstance(ports.model, ScriptedModel)
    assert isinstance(ports.engine, FakeEngine)
    assert isinstance(ports.config, InMemoryConfig)
    assert isinstance(ports.telemetry, InMemoryTelemetry)


def test_inprocess_is_refused_outside_fake_and_local() -> None:
    """Exit criterion (ADR-001 item 4): `inprocess` is for the chassis's tests and local runs."""
    check_lane("inprocess", "fake")
    check_lane("inprocess", "local")
    with pytest.raises(LaneNotAllowed):
        check_lane("inprocess", "cloud")


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
    """Day-0 scope: the import-lint rule. No product SDK outside its adapter, no framework."""
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
