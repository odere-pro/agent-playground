"""138 CH-18: the judge per agent.

- `spec.harness.rubric` names a rubric and its version; an unknown name is refused at load, and
  the judge sends the named rubric as its system message.
- The gate score reaches `FeedbackPort` as a score named after the rubric version.
- `spec.harness.shadow_evaluator` with `shadow_sample_rate: 1.0`: both judges score each attempt,
  only the main one gates, and `chassis.evaluator_agreement{pair, agree}` counts the result.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from chassis import profiles, rubrics
from chassis.adapters.evaluators import JudgeEvaluator
from chassis.fakes import ScriptedEvaluator
from chassis.fakes.feedback import InMemoryFeedback
from chassis.fakes.model import ScriptedModel
from chassis.ports.evaluator import EvalCase, Score
from chassis.server.evaluator_gate import (
    EVALUATOR_AGREEMENT,
    EVALUATOR_CALLS,
    GATE_FEEDBACK_FAILED,
    GATE_RETRIES,
)
from chassis.server.observability import TOKENS
from pydantic import ValidationError
from test_evaluator_gate import BAD, GOOD, RouteEngine, config, evaluator, ports, run, telemetry

TEST_RUBRIC = "test-brevity-v2"
TEST_RUBRIC_TEXT = "Rubric under test: score 1.0 when the answer is short."


@pytest.fixture
def test_rubric(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setitem(rubrics.RUBRICS, TEST_RUBRIC, TEST_RUBRIC_TEXT)
    return TEST_RUBRIC


def test_an_unknown_rubric_is_refused_at_load() -> None:
    with pytest.raises(ValidationError, match="unknown rubric 'no-such-rubric-v9'"):
        config(rubric="no-such-rubric-v9")


def test_the_default_rubric_is_the_pinned_facts_rubric() -> None:
    assert config().spec.harness.rubric == rubrics.DEFAULT_RUBRIC == "judge-facts-v1"


async def test_the_judge_sends_the_named_rubric_with_the_fake_model(test_rubric: str) -> None:
    # Arrange
    cfg = config(rubric=test_rubric)
    model = ScriptedModel(default_reply='{"score": 1.0, "reason": "short"}')
    judge = JudgeEvaluator(model)
    judge.bind_rubric(lambda: cfg.spec.harness.rubric)
    # Act
    await judge.score(EvalCase(input="simplify: x", output="x"))
    # Assert
    messages = model.calls[0]
    assert messages[0].role == "system"
    assert messages[0].content == TEST_RUBRIC_TEXT


async def test_the_gate_score_reaches_feedback_named_after_the_rubric_version(
    test_rubric: str,
) -> None:
    # Arrange
    feedback = InMemoryFeedback()
    bundle = ports(RouteEngine(), ScriptedEvaluator([], default=0.9))
    bundle = dataclasses.replace(bundle, feedback=feedback)
    # Act
    res, _ = await run(config(rubric=test_rubric, retries=0), bundle)
    # Assert
    body: dict[str, Any] = res.json()
    assert body["status"] == "ok", body
    [stored] = feedback.records.values()
    assert feedback.get(stored.feedback_id) == stored
    assert stored.name == test_rubric
    assert stored.score == 0.9
    assert stored.request_id == body["request_id"]
    assert stored.feedback_id == f"{body['request_id']}:{test_rubric}:1"


class ShadowEvaluator(ScriptedEvaluator):
    name = "shadow"


async def test_a_shadow_judge_scores_every_attempt_but_only_the_main_one_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the main judge passes every answer, the shadow fails every answer
    main, shadow = ScriptedEvaluator([], default=1.0), ShadowEvaluator([], default=0.0)
    monkeypatch.setitem(profiles.REGISTRY["evaluator"], "shadow-test", lambda: shadow)
    bundle = ports(RouteEngine(), main)
    cfg = config(shadow_evaluator="shadow-test", shadow_sample_rate=1.0)
    # Act
    res, _ = await run(cfg, bundle)
    # Assert: the main judge gates, so the answer passes on the first attempt
    body: dict[str, Any] = res.json()
    assert body["status"] == "ok", body
    assert len(main.cases) == 1
    assert [c.output for c in shadow.cases] == [c.output for c in main.cases]
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 0
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode="sync", outcome="pass") == 1
    assert t.counter_value(EVALUATOR_AGREEMENT, pair="fake:shadow", agree="false") == 1
    assert t.counter_value(EVALUATOR_AGREEMENT, pair="fake:shadow", agree="true") == 0


async def test_the_shadow_scores_each_attempt_of_a_retried_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the first answer fails the main judge, the second passes; the shadow passes all
    main, shadow = evaluator(), ShadowEvaluator([], default=1.0)
    monkeypatch.setitem(profiles.REGISTRY["evaluator"], "shadow-test", lambda: shadow)
    feedback = InMemoryFeedback()
    bundle = dataclasses.replace(ports(RouteEngine([BAD, GOOD]), main), feedback=feedback)
    cfg = config(shadow_evaluator="shadow-test", shadow_sample_rate=1.0, retries=1)
    # Act
    res, _ = await run(cfg, bundle)
    # Assert
    body: dict[str, Any] = res.json()
    assert body["status"] == "ok", body
    assert len(main.cases) == 2
    assert len(shadow.cases) == 2
    t = telemetry(bundle)
    assert t.counter_value(GATE_RETRIES) == 1
    assert t.counter_value(EVALUATOR_AGREEMENT, pair="fake:shadow", agree="false") == 1
    assert t.counter_value(EVALUATOR_AGREEMENT, pair="fake:shadow", agree="true") == 1
    ids = sorted(r.feedback_id for r in feedback.records.values())
    rid = body["request_id"]
    assert ids == [f"{rid}:{rubrics.DEFAULT_RUBRIC}:1", f"{rid}:{rubrics.DEFAULT_RUBRIC}:2"]


async def test_a_zero_shadow_rate_makes_no_shadow_call(monkeypatch: pytest.MonkeyPatch) -> None:
    shadow = ShadowEvaluator([], default=0.0)
    monkeypatch.setitem(profiles.REGISTRY["evaluator"], "shadow-test", lambda: shadow)
    bundle = ports(RouteEngine(), ScriptedEvaluator([], default=1.0))
    res, _ = await run(config(shadow_evaluator="shadow-test", shadow_sample_rate=0.0), bundle)
    assert res.json()["status"] == "ok"
    assert shadow.cases == []


class CostlyShadow(ShadowEvaluator):
    """A shadow that reports judge usage, as `JudgeEvaluator` does."""

    async def score(self, case: EvalCase) -> Score:
        base = await super().score(case)
        return dataclasses.replace(base, input_tokens=30, output_tokens=5)


async def test_the_shadow_judges_tokens_are_counted_under_its_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    shadow = CostlyShadow([], default=1.0)
    monkeypatch.setitem(profiles.REGISTRY["evaluator"], "shadow-test", lambda: shadow)
    bundle = ports(RouteEngine(), ScriptedEvaluator([], default=1.0))
    # Act
    await run(config(shadow_evaluator="shadow-test", shadow_sample_rate=1.0), bundle)
    # Assert: 042 A-2 judge usage covers the shadow too
    t = telemetry(bundle)
    assert t.counter_value(TOKENS, route="evaluator:shadow", direction="input") == 30
    assert t.counter_value(TOKENS, route="evaluator:shadow", direction="output") == 5


class BrokenFeedback(InMemoryFeedback):
    async def submit(self, feedback: Any) -> None:
        raise RuntimeError("store bug")


async def test_a_feedback_store_bug_never_fails_the_run() -> None:
    bundle = dataclasses.replace(
        ports(RouteEngine(), ScriptedEvaluator([], default=1.0)), feedback=BrokenFeedback()
    )
    res, _ = await run(config(), bundle)
    assert res.json()["status"] == "ok"
    assert telemetry(bundle).counter_value(GATE_FEEDBACK_FAILED, code="internal") == 1
