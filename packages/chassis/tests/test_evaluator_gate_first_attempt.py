"""046 A-5: the run-level first-attempt counter, an additive contract change (decided
2026-11-13).

`chassis.gate_first_attempt_passes{mode}` (suggested name) counts one per gated run whose first
attempt passed the gate, so the first-try pass rate is exact: that counter over
`chassis.gate_runs{mode}`. The attempt counters cannot give it: a run that retries and then falls
back adds to both `gate_retries` and `gate_fallbacks`, and a run with two retries adds 2.

Not a first-attempt pass: a retry that passes, a fallback, an evaluator outage on attempt 1
(`fail_open` ships it unscored), an engine error, and a structured answer (never judged). In
async mode, and every stream, the one background score counts under `mode="async"` when it
passes; a dropped or failed score does not.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from chassis.fakes import ScriptedEvaluator
from chassis.ports.evaluator import EvalCase, EvaluatorError, Score
from chassis.server import create_app
from chassis.server.evaluator_gate import (
    EVALUATOR_CALLS,
    GATE_FALLBACKS,
    GATE_FIRST_ATTEMPT_PASSES,
    GATE_RETRIES,
    GATE_RUNS,
)
from poc_harness.promtext import samples
from test_evaluator_gate import (
    BAD,
    GOOD,
    INPUT,
    RouteEngine,
    client,
    config,
    evaluator,
    ports,
    run,
    telemetry,
)
from test_evaluator_gate_skip import METRICS_SPEC, StructuredEngine

FIRST = "chassis_gate_first_attempt_passes_total"


class _Outage(ScriptedEvaluator):
    """Raises on every scoring."""

    def __init__(self) -> None:
        super().__init__([], default=0.9)

    async def score(self, case: EvalCase) -> Score:
        raise EvaluatorError("evaluator_unavailable", "scripted outage", retryable=True)


async def test_a_run_that_passes_on_its_first_attempt_counts_once() -> None:
    # Arrange
    bundle = ports(RouteEngine(answers=[GOOD]), evaluator())
    # Act
    res, _ = await run(config(), bundle)
    # Assert
    assert res.json()["status"] == "ok"
    t = telemetry(bundle)
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 1
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="async") == 0


async def test_a_retry_that_passes_is_not_a_first_attempt_pass() -> None:
    bundle = ports(RouteEngine(answers=[BAD, GOOD]), evaluator())
    res, _ = await run(config(), bundle)
    assert res.json()["status"] == "ok" and res.json()["metrics"]["attempts"] == 2
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 1
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 0


async def test_a_run_sent_to_the_fallback_is_not_a_first_attempt_pass() -> None:
    bundle = ports(RouteEngine(), evaluator())
    res, _ = await run(config(retries=2), bundle)
    assert res.json()["status"] == "fallback"
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 2 and t.counter_value(GATE_FALLBACKS) == 1
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 0


async def test_an_unscored_first_attempt_is_not_a_pass() -> None:
    """`fail_open` ships the answer, but the gate never passed it."""
    bundle = ports(RouteEngine(answers=[GOOD]), _Outage())
    res, _ = await run(config(evaluator_on_error="fail_open"), bundle)
    assert res.json()["status"] == "ok"
    assert telemetry(bundle).counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 0


async def test_an_engine_error_on_the_first_attempt_is_not_a_pass() -> None:
    bundle = ports(RouteEngine(error_first=True), evaluator())
    res, _ = await run(config(), bundle)
    assert res.json()["status"] == "error"
    assert telemetry(bundle).counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 0


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_structured_answer_is_not_a_pass(mode: str) -> None:
    """138 CH-18: a structured answer is never judged, so it never passed the gate."""
    bundle = ports(StructuredEngine(), ScriptedEvaluator([], default=0.9))
    res, _ = await run(config(evaluator_mode=mode), bundle)
    assert res.json()["status"] == "ok"
    assert telemetry(bundle).counter_value(GATE_FIRST_ATTEMPT_PASSES, mode=mode) == 0


@pytest.mark.parametrize(("answer", "count"), [(GOOD, 1), (BAD, 0)])
async def test_async_mode_counts_a_passing_background_score_once(answer: str, count: int) -> None:
    # Arrange
    bundle = ports(RouteEngine(answers=[answer]), evaluator())
    # Act: `run` waits for the background score
    res, _ = await run(config(evaluator_mode="async"), bundle)
    # Assert: async never retries, so its one score is its first attempt
    assert res.json()["output"]["text"] == answer
    t = telemetry(bundle)
    assert t.counter_value(GATE_RUNS, mode="async") == 1
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="async") == count
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 0


async def test_a_failed_background_score_is_not_a_pass() -> None:
    bundle = ports(RouteEngine(answers=[GOOD]), _Outage())
    await run(config(evaluator_mode="async"), bundle)
    t = telemetry(bundle)
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="async", outcome="error") == 1
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="async") == 0


async def test_a_stream_counts_under_mode_async_even_when_the_mode_is_sync() -> None:
    bundle = ports(RouteEngine(answers=[GOOD]), evaluator())
    res, _ = await run(config(evaluator_mode="sync"), bundle, stream=True)
    assert res.status_code == 200 and GOOD in res.text
    t = telemetry(bundle)
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="async") == 1
    assert t.counter_value(GATE_FIRST_ATTEMPT_PASSES, mode="sync") == 0


def _value(text: str, name: str, **labels: str) -> float:
    found = samples(text)
    key = (name, frozenset({"agent": "simplifier", **labels}.items()))
    assert key in found, f"{key} not in /metrics"
    return found[key]


async def _three_runs_then_scrape() -> str:
    """Run 1 passes on attempt 1; run 2 fails, retries, and passes; run 3 fails, retries, fails,
    and goes to the fallback. Then `GET /metrics`."""
    engine = RouteEngine(answers=[GOOD, BAD, GOOD, BAD, BAD])
    app = create_app(config(), ports(engine, evaluator()))
    async with client(app) as c, app.router.lifespan_context(app):
        bodies: list[dict[str, Any]] = []
        for _ in range(3):
            res = await c.post("/v1/run", json={"input": {"text": INPUT}})
            bodies.append(res.json())
        assert [b["status"] for b in bodies] == ["ok", "ok", "fallback"], bodies
        scrape: httpx.Response = await c.get("/metrics")
    return scrape.text


async def test_the_first_try_rate_is_exact_where_the_attempt_counters_are_not() -> None:
    # Act
    text = await _three_runs_then_scrape()
    # Assert: one of three runs passed on attempt 1
    runs = _value(text, "chassis_gate_runs_total", mode="sync")
    retries = _value(text, "chassis_gate_retries_total")
    fallbacks = _value(text, "chassis_gate_fallbacks_total")
    first = _value(text, FIRST, mode="sync")
    assert (runs, retries, fallbacks, first) == (3, 2, 1, 1)
    assert first / runs == pytest.approx(1 / 3)
    assert 1 - (retries + fallbacks) / runs == 0  # the old estimate reads 0%
    assert 1 - fallbacks / runs == pytest.approx(2 / 3)  # the old upper bound reads 67%


async def test_the_counter_is_zero_for_both_modes_with_the_gate_on() -> None:
    app = create_app(config(), ports(RouteEngine(), evaluator()))
    async with client(app) as c, app.router.lifespan_context(app):
        lines = (await c.get("/metrics")).text.splitlines()
    for mode in ("sync", "async"):
        assert f'{FIRST}{{agent="simplifier",mode="{mode}"}} 0' in lines, mode
    assert f"# TYPE {FIRST} counter" in lines


def test_the_metrics_spec_gives_the_exact_first_attempt_pass_rate() -> None:
    text = METRICS_SPEC.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| First-attempt pass rate"))
    assert f"`{FIRST}{{mode}}` / `chassis_gate_runs_total{{mode}}`" in row


async def test_the_counter_has_no_series_with_the_gate_off() -> None:
    app = create_app(config(evaluator_gate=False), ports(RouteEngine(), evaluator()))
    async with client(app) as c, app.router.lifespan_context(app):
        text = (await c.get("/metrics")).text
    assert FIRST not in {name for name, _ in samples(text)}
    assert f"# TYPE {FIRST} counter" in text.splitlines()  # declared, as every gate family
