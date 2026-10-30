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

import pytest
from chassis.server.evaluator_gate import (
    EVALUATOR_CALLS,
    GATE_FALLBACKS,
    GATE_RETRIES,
    SCORE_SPAN,
)
from poc_harness.bakeoff_tasks import ROUTE
from poc_harness.evals import (
    BAD,
    FALLBACK_ROUTE,
    GOOD,
    JUDGE_ROUTE,
    gated_run,
    routes,
    serve_gate_backends,
)
from poc_harness.sidecar_lane import Backends

TWO_ENGINES = ["echo-python", "echo-langgraph"]


@pytest.fixture(scope="module")
def backends() -> Iterator[Backends]:
    with serve_gate_backends() as served:
        yield served


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
