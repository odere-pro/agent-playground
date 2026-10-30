"""PoC-8 exit criterion 6: "The online evaluator gate retries and falls back as configured."
(docs/planning/poc/008-PoC-8-build-cross-cutting.md)

Each cell runs the gate input (`GATE_MARK`) through the chassis with every stage on. On the main
route the model answers `BAD`, which `ScriptedEvaluator` scores 0.1, below the threshold. Both
`retries` and `fallback_route` come from the config (`spec.harness.retries`,
`spec.model.fallback_route`); `retries: 2` here, not the default 1, so the count is the config's.

- With a fallback route: `retries + 1` scored attempts on the main route, then one attempt on the
  fallback route, which the model proxy forces even when the workload names another. The answer
  is the fallback's (`GOOD`), the end status is `fallback`, and the bad answer never leaves.
- With no fallback route: the run ends in the gate's error, `evaluator_gate_failed`, no answer.

The gate wraps the lane's connector, so the same code reruns an in-process handle, a sidecar, and
a remote workload.
"""

from __future__ import annotations

import pytest
from chassis.server.evaluator_gate import (
    EVALUATOR_CALLS,
    GATE_FAILED,
    GATE_FALLBACKS,
    GATE_RETRIES,
)
from poc_harness.full_stack import cell_params, full_stack
from poc_harness.stage_model import BAD, FALLBACK_ROUTE, GATE_MARK, GOOD, ROUTE

RETRIES = 2
GATE_INPUT = f"simplify: {GATE_MARK} The report was signed by Dana Ruiz on 4 May 2025."


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_the_gate_retries_then_falls_back_to_the_configured_route(
    engine: str, lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 6: `retries` retries, then the `fallback_route` answer with status `fallback`."""
    with full_stack(engine, lane, monkeypatch, retries=RETRIES) as stack:
        response = await stack.run(GATE_INPUT)
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "fallback", body
    assert body["output"]["text"] == GOOD
    assert BAD not in response.text
    assert stack.model.routes == [ROUTE] * (RETRIES + 1) + [FALLBACK_ROUTE]
    assert body["metrics"]["attempts"] == RETRIES + 2
    assert body["metrics"]["model_route"] == FALLBACK_ROUTE
    assert len(stack.evaluator.cases) == RETRIES + 1, "the fallback answer is not scored"
    assert stack.telemetry.counter_value(GATE_RETRIES) == RETRIES
    assert stack.telemetry.counter_value(GATE_FALLBACKS) == 1
    fails = stack.telemetry.counter_value(
        EVALUATOR_CALLS, evaluator="fake", mode="sync", outcome="fail"
    )
    assert fails == RETRIES + 1


@pytest.mark.parametrize(("engine", "lane"), cell_params())
async def test_with_no_fallback_route_the_run_ends_in_the_gate_error(
    engine: str, lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 6: no `fallback_route` in the config, so after the retries the run ends with one
    `evaluator_gate_failed` error and no answer."""
    with full_stack(engine, lane, monkeypatch, retries=RETRIES, fallback_route=None) as stack:
        response = await stack.run(GATE_INPUT)
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["output"]["error"]["code"] == GATE_FAILED, body
    assert BAD not in response.text
    assert stack.model.routes == [ROUTE] * (RETRIES + 1)
    assert stack.telemetry.counter_value(GATE_RETRIES) == RETRIES
    assert stack.telemetry.counter_value(GATE_FALLBACKS) == 0
