"""046 A-5: the run-level first-attempt counter, an additive contract change (decided
2026-11-13).

`chassis.gate_first_attempts{mode, outcome}` (suggested name) counts one per gated run whose
first attempt was scored, by `outcome` (`pass` or `fail`). The first-try pass rate is
`pass / (pass + fail)`: exact over the runs the gate scored. `chassis.gate_runs{mode}` is not its
denominator: it also counts sampled runs never scored (an engine error, a structured answer, an
evaluator outage, an async score dropped or abandoned). The attempt counters cannot give it
either: a run that retries and then falls back adds to both `gate_retries` and `gate_fallbacks`.

It is counted inside the scoring, right after the verdict, so the span, `evaluator_calls`, and
this counter agree even when a background score is cancelled while it stores its feedback. The
scoring takes `threshold` and `rubric` from the caller's one config read.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from chassis import rubrics
from chassis.core.envelope import Context, Request, TaskInput
from chassis.core.events import Delta, End, Event, Start
from chassis.fakes import InMemoryTelemetry, ScriptedEvaluator
from chassis.fakes.feedback import InMemoryFeedback
from chassis.ports.evaluator import EvalCase, EvaluatorError, Score
from chassis.server import ChassisConfig, create_app, evaluator_gate
from chassis.server.evaluator_gate import (
    EVALUATOR_ABANDONED,
    EVALUATOR_CALLS,
    GATE_FALLBACKS,
    GATE_FIRST_ATTEMPTS,
    GATE_RETRIES,
    GATE_RUNS,
    GatedEngine,
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

FIRST = "chassis_gate_first_attempts_total"
OUTCOMES = ("pass", "fail")
OTHER_RUBRIC = "test-other-rubric-v1"


def counts(t: InMemoryTelemetry, mode: str) -> tuple[int, int]:
    """(pass, fail) of the first-attempt counter for `mode`."""
    return (
        t.counter_value(GATE_FIRST_ATTEMPTS, mode=mode, outcome="pass"),
        t.counter_value(GATE_FIRST_ATTEMPTS, mode=mode, outcome="fail"),
    )


class _Outage(ScriptedEvaluator):
    """Raises on every scoring."""

    def __init__(self) -> None:
        super().__init__([], default=0.9)

    async def score(self, case: EvalCase) -> Score:
        raise EvaluatorError("evaluator_unavailable", "scripted outage", retryable=True)


async def test_a_run_that_passes_on_its_first_attempt_counts_one_pass() -> None:
    # Arrange
    bundle = ports(RouteEngine(answers=[GOOD]), evaluator())
    # Act
    res, _ = await run(config(), bundle)
    # Assert
    assert res.json()["status"] == "ok"
    t = telemetry(bundle)
    assert counts(t, "sync") == (1, 0)
    assert counts(t, "async") == (0, 0)


async def test_a_retry_that_passes_counts_one_first_attempt_fail() -> None:
    bundle = ports(RouteEngine(answers=[BAD, GOOD]), evaluator())
    res, _ = await run(config(), bundle)
    assert res.json()["status"] == "ok" and res.json()["metrics"]["attempts"] == 2
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 1
    assert counts(t, "sync") == (0, 1)


async def test_a_run_sent_to_the_fallback_counts_only_its_first_attempt() -> None:
    bundle = ports(RouteEngine(), evaluator())
    res, _ = await run(config(retries=2), bundle)
    assert res.json()["status"] == "fallback"
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 2 and t.counter_value(GATE_FALLBACKS) == 1
    assert counts(t, "sync") == (0, 1)  # three scored attempts, one first attempt


async def test_an_unscored_first_attempt_is_not_counted() -> None:
    """`fail_open` ships the answer unscored: no verdict, no count."""
    bundle = ports(RouteEngine(answers=[GOOD]), _Outage())
    res, _ = await run(config(evaluator_on_error="fail_open"), bundle)
    assert res.json()["status"] == "ok"
    assert counts(telemetry(bundle), "sync") == (0, 0)


async def test_an_engine_error_on_the_first_attempt_is_not_counted() -> None:
    bundle = ports(RouteEngine(error_first=True), evaluator())
    res, _ = await run(config(), bundle)
    assert res.json()["status"] == "error"
    t = telemetry(bundle)
    assert t.counter_value(GATE_RUNS, mode="sync") == 1
    assert counts(t, "sync") == (0, 0)


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_structured_answer_is_not_counted(mode: str) -> None:
    """138 CH-18: a structured answer is never judged."""
    bundle = ports(StructuredEngine(), ScriptedEvaluator([], default=0.9))
    res, _ = await run(config(evaluator_mode=mode), bundle)
    assert res.json()["status"] == "ok"
    assert counts(telemetry(bundle), mode) == (0, 0)


@pytest.mark.parametrize(("answer", "expected"), [(GOOD, (1, 0)), (BAD, (0, 1))])
async def test_async_mode_counts_the_background_score_once(
    answer: str, expected: tuple[int, int]
) -> None:
    # Arrange
    bundle = ports(RouteEngine(answers=[answer]), evaluator())
    # Act: `run` waits for the background score
    res, _ = await run(config(evaluator_mode="async"), bundle)
    # Assert: async never retries, so its one score is its first attempt
    assert res.json()["output"]["text"] == answer
    t = telemetry(bundle)
    assert t.counter_value(GATE_RUNS, mode="async") == 1
    assert counts(t, "async") == expected
    assert counts(t, "sync") == (0, 0)


async def test_a_failed_background_score_is_not_counted() -> None:
    bundle = ports(RouteEngine(answers=[GOOD]), _Outage())
    await run(config(evaluator_mode="async"), bundle)
    t = telemetry(bundle)
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="async", outcome="error") == 1
    assert counts(t, "async") == (0, 0)


async def test_a_stream_counts_under_mode_async_even_when_the_mode_is_sync() -> None:
    bundle = ports(RouteEngine(answers=[GOOD]), evaluator())
    res, _ = await run(config(evaluator_mode="sync"), bundle, stream=True)
    assert res.status_code == 200 and GOOD in res.text
    t = telemetry(bundle)
    assert counts(t, "async") == (1, 0)
    assert counts(t, "sync") == (0, 0)


class _HangingFeedback(InMemoryFeedback):
    """`submit` never returns; `entered` is set once it is called."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def submit(self, feedback: Any) -> None:
        self.entered.set()
        await asyncio.Event().wait()


async def test_a_background_score_cancelled_while_storing_its_feedback_is_still_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the feedback store hangs, and `close()` waits almost no time before it cancels
    monkeypatch.setattr(evaluator_gate, "SHUTDOWN_WAIT_S", 0.01)
    feedback = _HangingFeedback()
    bundle = dataclasses.replace(ports(RouteEngine(answers=[GOOD]), evaluator()), feedback=feedback)
    app = create_app(config(evaluator_mode="async"), bundle)
    async with client(app) as c, app.router.lifespan_context(app):
        res = await c.post("/v1/run", json={"input": {"text": INPUT}})
        await asyncio.wait_for(feedback.entered.wait(), 5)
        gate = app.state.ports.engine
        assert isinstance(gate, GatedEngine)
        # Act: shut the gate down between the score and the feedback write
        await gate.close()
    # Assert: the verdict, the span, and the counter agree
    assert res.json()["output"]["text"] == GOOD
    t = telemetry(bundle)
    assert t.counter_value(EVALUATOR_ABANDONED) == 1
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="async", outcome="pass") == 1
    assert counts(t, "async") == (1, 0)


@pytest.fixture
def other_rubric(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setitem(rubrics.RUBRICS, OTHER_RUBRIC, "Score anything 0.")
    yield OTHER_RUBRIC


class _ReloadingEngine(RouteEngine):
    """Answers GOOD and, mid-attempt, reloads the config to a stricter threshold and another
    rubric, as `ConfigPort` would."""

    def __init__(self, live: list[ChassisConfig], reloaded: ChassisConfig) -> None:
        super().__init__(answers=[GOOD])
        self.live, self.reloaded = live, reloaded

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Delta(text=GOOD)
        self.live[0] = self.reloaded
        yield End()


async def test_a_threshold_reload_between_attempt_one_and_its_scoring_is_one_decision(
    request_: Request, context: Context, other_rubric: str
) -> None:
    # Arrange: run-start threshold 0.7 and the default rubric; the judge scores GOOD at 0.9
    live = [config(threshold=0.7)]
    engine = _ReloadingEngine(live, config(threshold=0.95, rubric=other_rubric))
    t, feedback = InMemoryTelemetry(), InMemoryFeedback()
    gate = GatedEngine(engine, evaluator(), config=lambda: live[0], telemetry=t, feedback=feedback)
    # Act
    events = [e async for e in gate.run(request_, context)]
    # Assert: the run-start config decided, and every record says so
    assert live[0].spec.harness.threshold == 0.95, "the reload landed"
    assert [e.text for e in events if isinstance(e, Delta)] == [GOOD]
    assert t.counter_value(GATE_RETRIES) == 0
    span = next(s for s in t.spans if s.name == "chassis.evaluator.score")
    assert span.attributes["evaluator.threshold"] == 0.7
    assert span.attributes["evaluator.passed"] is True
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="sync", outcome="pass") == 1
    assert counts(t, "sync") == (1, 0)
    [stored] = feedback.records.values()
    assert stored.name == rubrics.DEFAULT_RUBRIC


def _value(text: str, name: str, **labels: str) -> float:
    found = samples(text)
    key = (name, frozenset({"agent": "simplifier", **labels}.items()))
    assert key in found, f"{key} not in /metrics"
    return found[key]


async def _four_runs_then_scrape() -> str:
    """Run 1 passes on attempt 1; run 2 fails, retries, and passes; run 3 fails, retries, fails,
    and goes to the fallback; run 4 is a structured answer, never scored. Then `GET /metrics`."""
    engine = RouteEngine(answers=[GOOD, BAD, GOOD, BAD, BAD])
    app = create_app(config(), ports(engine, evaluator()))
    async with client(app) as c, app.router.lifespan_context(app):
        bodies: list[dict[str, Any]] = []
        for _ in range(3):
            res = await c.post("/v1/run", json={"input": {"text": INPUT}})
            bodies.append(res.json())
        assert [b["status"] for b in bodies] == ["ok", "ok", "fallback"], bodies
        app.state.ports.engine.inner = StructuredEngine()
        assert (await c.post("/v1/run", json={"input": {"text": INPUT}})).json()["status"] == "ok"
        scrape: httpx.Response = await c.get("/metrics")
    return scrape.text


async def test_the_first_try_rate_counts_only_scored_first_attempts() -> None:
    # Act
    text = await _four_runs_then_scrape()
    # Assert: one pass and two fails over four sampled runs
    runs = _value(text, "chassis_gate_runs_total", mode="sync")
    retries = _value(text, "chassis_gate_retries_total")
    fallbacks = _value(text, "chassis_gate_fallbacks_total")
    passed = _value(text, FIRST, mode="sync", outcome="pass")
    failed = _value(text, FIRST, mode="sync", outcome="fail")
    assert (runs, retries, fallbacks, passed, failed) == (4, 2, 1, 1, 2)
    assert passed / (passed + failed) == pytest.approx(1 / 3)
    assert passed / runs == pytest.approx(1 / 4)  # over gate runs: the unscored run drags it
    assert 1 - (retries + fallbacks) / runs == pytest.approx(1 / 4)


async def test_the_four_series_are_zero_with_the_gate_on() -> None:
    app = create_app(config(), ports(RouteEngine(), evaluator()))
    async with client(app) as c, app.router.lifespan_context(app):
        lines = (await c.get("/metrics")).text.splitlines()
    for mode in ("sync", "async"):
        for outcome in OUTCOMES:
            line = f'{FIRST}{{agent="simplifier",mode="{mode}",outcome="{outcome}"}} 0'
            assert line in lines, line
    assert f"# TYPE {FIRST} counter" in lines


async def test_the_counter_has_no_series_with_the_gate_off() -> None:
    app = create_app(config(evaluator_gate=False), ports(RouteEngine(), evaluator()))
    async with client(app) as c, app.router.lifespan_context(app):
        text = (await c.get("/metrics")).text
    assert FIRST not in {name for name, _ in samples(text)}
    assert f"# TYPE {FIRST} counter" in text.splitlines()  # declared, as every gate family


def test_the_metrics_spec_gives_the_first_attempt_pass_rate_formula() -> None:
    """Replaces the 138 CH-18 check of the old per-scoring formula."""
    text = METRICS_SPEC.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| First-attempt pass rate"))
    formula = f'`{FIRST}{{mode, outcome="pass"}}` / `{FIRST}{{mode, outcome in pass, fail}}`'
    assert formula in row
