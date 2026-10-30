"""The evaluator gate on a sidecar engine, against fake backends on Unix sockets.

- `gate_script()`: a fake model script with one gate case (a bad answer on the default route, a
  good one on the fallback route) and a scripted judge in the chassis `JudgeEvaluator` format. The
  judge's scores are scripted, so they show the mechanics only, never judge quality.
- `serve_gate_backends()`: the fake model server on `gate_script()` and the two-tool fake MCP
  server, each on a Unix socket (`sidecar_lane.Backends`), so a sidecar workload and the judge reach
  them with no TCP.
- `gated_run()`: one `/v1/run` of the gate input through a gated chassis; `routes()` reads the
  model routes of its calls.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.adapters.evaluators import JudgeEvaluator
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import GatedEngine
from fake_mcp_server.server import FakeMcpState
from fake_mcp_server.server import create_app as create_tool_app
from fake_model_server import Script
from fake_model_server import create_app as create_model_app

from poc_harness.apps import fake_model_over_litellm
from poc_harness.bakeoff_tasks import ENGINES, ROUTE
from poc_harness.servers import a2a_on_unix_socket
from poc_harness.sidecar_lane import Backends, Recorder, Seen, wire_python_engine
from poc_harness.sockets import on_unix_socket

FALLBACK_ROUTE = "fallback-route"
JUDGE_ROUTE = "judge"
GATE_INPUT = "simplify: gate-check The report was signed by Dana Ruiz on 4 May 2025."
BAD = "The report was signed."
GOOD = "Dana Ruiz signed the report on 4 May 2025."
JUDGE_USAGE = {"prompt_tokens": 300, "completion_tokens": 25}
"""Scripted judge usage; the real count of the rubric prompt is in the gate cost note."""
CONTRACT_BAD = "It is a model. It is cheap."


def judge_rules(bad: list[str]) -> list[dict[str, Any]]:
    rules = [
        {
            "match": f"<answer>\n{answer}\n</answer>",
            "reply": json.dumps({"score": 0.1, "reason": "drops facts"}),
            "usage": dict(JUDGE_USAGE),
        }
        for answer in bad
    ]
    rules.append(
        {
            "match": "</answer>",
            "reply": json.dumps({"score": 0.95, "reason": "keeps the facts"}),
            "usage": dict(JUDGE_USAGE),
        }
    )
    return rules


def gate_script() -> Script:
    return Script.model_validate(
        {
            "rules": [
                *judge_rules([BAD, CONTRACT_BAD]),
                {"model": FALLBACK_ROUTE, "match": GATE_INPUT, "reply": GOOD},
                {"match": GATE_INPUT, "reply": BAD},
            ],
            "default_reply": "ok",
        }
    )


@contextmanager
def serve_gate_backends() -> Iterator[Backends]:
    model_app = create_model_app(gate_script())
    state = FakeMcpState()
    model_seen: list[Seen] = []
    tool_seen: list[Seen] = []
    with (
        on_unix_socket(Recorder(model_app, model_seen), "model.sock") as model_uds,
        on_unix_socket(Recorder(create_tool_app(state), tool_seen), "tools.sock") as tool_uds,
    ):
        yield Backends(model_uds, tool_uds, model_app, state, model_seen, tool_seen)


def gate_config(url: str, uds: str, **harness: Any) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-poc07-gate",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "sidecar", "url": url, "uds": uds},
                "model": {"route": ROUTE, "fallback_route": FALLBACK_ROUTE},
                "harness": {"evaluator_gate": True, "retries": 1, "threshold": 0.7, **harness},
            },
        }
    )


async def gated_run(
    engine: str,
    backends: Backends,
    monkeypatch: pytest.MonkeyPatch,
    **harness: Any,
) -> tuple[dict[str, Any], InMemoryTelemetry, list[dict[str, Any]]]:
    """One `/v1/run` of the gate input through the chassis, the engine as a sidecar. Returns the
    response, the telemetry, and the model request bodies the fake server received."""
    backends.reset()
    wire_python_engine(monkeypatch, ENGINES[engine], backends)
    judge = JudgeEvaluator(
        LiteLLMModel(
            "http://model.invalid/v1", transport=httpx.AsyncHTTPTransport(uds=backends.model_uds)
        ),
        route=JUDGE_ROUTE,
    )
    telemetry = InMemoryTelemetry()
    with a2a_on_unix_socket(ENGINES[engine].handle) as (url, uds):
        bundle = PortBundle(
            model=fake_model_over_litellm(),
            engine=SidecarConnector(),
            config=InMemoryConfig(),
            telemetry=telemetry,
            evaluator=judge,
        )
        app = create_app(gate_config(url, uds, **harness), bundle)
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://chassis") as client,
            app.router.lifespan_context(app),
        ):
            res = await client.post("/v1/run", json={"input": {"text": GATE_INPUT}})
            gate = app.state.ports.engine
            if isinstance(gate, GatedEngine):
                assert await gate.wait(10) == 0
    assert res.status_code == 200, res.text
    return res.json(), telemetry, list(backends.model_app.state.calls)


def routes(calls: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("model")) for c in calls]
