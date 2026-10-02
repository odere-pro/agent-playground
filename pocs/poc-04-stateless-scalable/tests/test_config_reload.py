"""Exit criterion 7 and the scope item "Config loader", offline: a config change reaches the next
request with no restart, and a bad config is refused while the agent keeps the last good one.

A real chassis app over fake ports. The store is `InMemoryConfig`; `put` plays the store change
(`spec.adapters.config: memory`, so the bootstrap is the first config). The same drill against a
real MinIO through `S3Config` is `test_config_minio.py` (`network`).
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
import yaml
from chassis.core.handle import echo
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.schemas import SCHEMA_DIR
from chassis.server import ChassisConfig, create_app
from fastapi import FastAPI
from pydantic import ValidationError

BAD = Path(__file__).parent / "fixtures" / "bad-configs"
INVALID = sorted(BAD.glob("invalid-*.yaml"))
RESTART = sorted(BAD.glob("restart-*.yaml"))

GOOD: dict[str, Any] = {
    "version": "good-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "adapters": {"config": "memory"},
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}


@asynccontextmanager
async def running() -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, InMemoryConfig, Any]]:
    store = InMemoryConfig()
    telemetry = InMemoryTelemetry()
    ports = PortBundle(
        model=ScriptedModel(),
        engine=FakeEngine(handle=echo),
        config=store,
        telemetry=telemetry,
    )
    app = create_app(ChassisConfig.model_validate(GOOD), ports)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
        app.router.lifespan_context(app),
    ):
        yield app, client, store, telemetry


async def run(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await client.post("/v1/run", json={"input": {"text": "hello"}, **body})


async def test_a_change_takes_effect_on_the_next_request_with_no_restart() -> None:
    async with running() as (app, client, store, telemetry):
        assert (await client.get("/ready")).status_code == 200
        before = (await run(client)).json()["versions"]
        assert before["config"] == "good-1"
        assert before["model_route"] == "fake-route"
        assert (await run(client, budget={"max_tokens": 3000})).status_code == 200

        changed = copy.deepcopy(GOOD)
        changed["version"] = "good-2"
        changed["spec"]["model"]["route"] = "route-2"
        changed["spec"]["prompt"]["version"] = "p2"
        changed["spec"]["limits"] = {"max_tokens_max": 2500, "body_bytes_max": 200}
        await store.put("echo", changed)

        after = (await run(client)).json()["versions"]
        assert after["config"].startswith("good-2+")  # `<version>+<content hash>`
        assert after == {
            **before,
            "config": after["config"],
            "model_route": "route-2",
            "prompt": "p2",
        }
        assert (await client.get("/manifest")).json()["versions"]["config"].startswith("good-2+")
        # spec.limits: the budget ceiling and the body cap are the new ones.
        over = await run(client, budget={"max_tokens": 3000})
        assert over.status_code == 400
        big = await run(client, padding="x" * 300)
        assert big.status_code == 413
        assert telemetry.counter_value("chassis.config.reloaded") == 1
        assert app.state.ports is not None  # same app, same ports: no restart


@pytest.mark.parametrize("path", INVALID + RESTART, ids=lambda p: p.stem)
async def test_a_bad_config_is_refused_and_the_last_good_one_stays(path: Path) -> None:
    bad = yaml.safe_load(path.read_text())  # noqa: ASYNC240 (a small fixture, read once)
    async with running() as (app, client, store, telemetry):
        good = app.state.config
        await store.put("echo", bad)
        assert app.state.config is good
        reason = "invalid" if path.stem.startswith("invalid-") else "restart_required"
        assert telemetry.counter_value("chassis.config.rejected", reason=reason) == 1
        res = await run(client)
        assert res.status_code == 200
        assert res.json()["versions"]["config"] == "good-1"
        assert res.json()["versions"]["model_route"] == "fake-route"
        assert (await client.get("/ready")).status_code == 200


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_every_invalid_fixture_fails_the_model_and_the_published_schema(path: Path) -> None:
    data = yaml.safe_load(path.read_text())
    with pytest.raises(ValidationError):
        ChassisConfig.model_validate(data)
    schema = json.loads((SCHEMA_DIR / "chassis-config.v0.json").read_text())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, schema)


@pytest.mark.parametrize("path", RESTART, ids=lambda p: p.stem)
def test_every_restart_fixture_is_a_valid_document(path: Path) -> None:
    ChassisConfig.model_validate(yaml.safe_load(path.read_text()))


def test_there_are_fixtures_of_both_kinds() -> None:
    assert len(INVALID) >= 4 and len(RESTART) >= 3
