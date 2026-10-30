"""PoC-7 exit criterion 5 (evals part): the evaluator gate works on two engines, echo-python and
echo-langgraph, in the `sidecar` lane over a Unix socket, with no engine-specific code. The
chassis app runs with the gate on and `JudgeEvaluator` over `LiteLLMModel`; the workload and the
judge both reach the fake model server on its socket. The judge's scores are scripted.

- With `retries: 1` and an answer the judge scores below the threshold, the run retries once,
  then falls back to `spec.model.fallback_route`, and the response has `status: fallback` (017
  H-4 acceptance).
- Async mode never changes the answer, and records the score.
- `evaluator_sample_rate: 0` makes no evaluator call.
- `evaluator_gate: false` makes no evaluator call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from chassis.adapters.a2a.sidecar import SidecarConnector
from chassis.adapters.evaluators import JudgeEvaluator
from chassis.adapters.litellm import LiteLLMModel
from chassis.fakes import InMemoryConfig, InMemoryTelemetry
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import (
    EVALUATOR_CALLS,
    GATE_FALLBACKS,
    GATE_RETRIES,
    SCORE_SPAN,
    GatedEngine,
)
from poc06_harness import ENGINES, ROUTE
from poc06a_harness import Backends, wire_python_engine
from poc07_evals_harness import (
    BAD,
    FALLBACK_ROUTE,
    GATE_INPUT,
    GOOD,
    JUDGE_ROUTE,
    serve_gate_backends,
)
from poc_harness.apps import fake_model_over_litellm
from poc_harness.servers import a2a_on_unix_socket

TWO_ENGINES = ["echo-python", "echo-langgraph"]


@pytest.fixture(scope="module")
def backends() -> Iterator[Backends]:
    with serve_gate_backends() as served:
        yield served


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


@pytest.mark.parametrize("engine", TWO_ENGINES)
async def test_poc07_evals_gate_retries_once_then_falls_back(
    engine: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5: "Each concern works on at least two engines": retry, then fallback."""
    body, telemetry, calls = await gated_run(engine, backends, monkeypatch)
    assert body["status"] == "fallback", body
    assert body["output"]["text"] == GOOD
    assert BAD not in body["output"]["text"]
    # attempt 1, judge, attempt 2 (retry), judge, then the fallback attempt; not scored
    assert routes(calls) == [ROUTE, JUDGE_ROUTE, ROUTE, JUDGE_ROUTE, FALLBACK_ROUTE]
    assert body["metrics"]["attempts"] == 3
    assert body["metrics"]["model_route"] == FALLBACK_ROUTE
    # the judge's scripted usage (2 x 300 in, 2 x 25 out) is in the request's metrics
    assert body["metrics"]["input_tokens"] >= 600 and body["metrics"]["output_tokens"] >= 50
    assert telemetry.counter_value(GATE_RETRIES) == 1
    assert telemetry.counter_value(GATE_FALLBACKS) == 1
    assert (
        telemetry.counter_value(EVALUATOR_CALLS, evaluator="judge", mode="sync", outcome="fail")
        == 2
    )


@pytest.mark.parametrize("engine", TWO_ENGINES)
async def test_poc07_evals_async_mode_never_changes_the_answer(
    engine: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5: "Each concern works on at least two engines": async keeps the answer."""
    body, telemetry, calls = await gated_run(engine, backends, monkeypatch, evaluator_mode="async")
    assert body["status"] == "ok" and body["output"]["text"] == BAD
    assert routes(calls) == [ROUTE, JUDGE_ROUTE]  # scored once, after the answer
    span = next(s for s in telemetry.spans if s.name == SCORE_SPAN)
    assert span.attributes["evaluator.score"] == 0.1
    assert span.attributes["evaluator.passed"] is False
    assert (
        telemetry.counter_value(EVALUATOR_CALLS, evaluator="judge", mode="async", outcome="fail")
        == 1
    )


@pytest.mark.parametrize("engine", TWO_ENGINES)
async def test_poc07_evals_sample_rate_zero_makes_no_evaluator_call(
    engine: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5: "Each concern works on at least two engines": rate 0 makes no judge call."""
    body, telemetry, calls = await gated_run(
        engine, backends, monkeypatch, evaluator_sample_rate=0.0
    )
    assert body["status"] == "ok" and body["output"]["text"] == BAD
    assert routes(calls) == [ROUTE]
    assert not any(s.name == SCORE_SPAN for s in telemetry.spans)


@pytest.mark.parametrize("engine", TWO_ENGINES)
async def test_poc07_evals_gate_off_makes_no_evaluator_call(
    engine: str, backends: Backends, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 5: "Each concern works on at least two engines": gate off makes no judge call."""
    body, telemetry, calls = await gated_run(engine, backends, monkeypatch, evaluator_gate=False)
    assert body["status"] == "ok" and body["output"]["text"] == BAD
    assert routes(calls) == [ROUTE]
    assert not any(s.name == SCORE_SPAN for s in telemetry.spans)
