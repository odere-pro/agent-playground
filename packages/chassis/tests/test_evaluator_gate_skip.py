"""138 CH-18: a structured answer (`end.output` with no `text`, and no `delta` text) is not
judged. The gate makes no evaluator call and counts it as
`chassis.evaluator_calls{outcome="skipped"}` (suggested), in sync and in async mode. Also: the
metrics spec gives the first-attempt pass rate formula."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import End, Event, Metrics, Start
from chassis.fakes import FakeEngine, ScriptedEvaluator
from chassis.server.evaluator_gate import EVALUATOR_CALLS, GATE_RETRIES
from test_evaluator_gate import config, ports, run, telemetry

STRUCTURED = {"label": "positive", "confidence": 0.93}
METRICS_SPEC = Path(__file__).resolve().parents[3] / "docs" / "specs" / "metrics.md"


class StructuredEngine(FakeEngine):
    """Answers with `end.output` only: no `delta`, no `text` key."""

    def __init__(self) -> None:
        super().__init__(handle=self._handle_)

    async def _handle_(self, input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        yield Metrics(input_tokens=40, output_tokens=10, model_route=ctx.model_route)
        yield End(output=STRUCTURED)


@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_a_structured_answer_makes_no_judge_call_and_counts_as_skipped(mode: str) -> None:
    # Arrange: a judge that would fail every answer
    ev = ScriptedEvaluator([], default=0.0)
    bundle = ports(StructuredEngine(), ev)
    # Act
    res, _ = await run(config(evaluator_mode=mode), bundle)
    # Assert
    body: dict[str, Any] = res.json()
    assert body["status"] == "ok", body
    assert body["output"] == STRUCTURED
    assert ev.cases == []
    t = telemetry(bundle)
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode=mode, outcome="skipped") == 1
    assert t.counter_value(EVALUATOR_CALLS, evaluator="fake", mode=mode, outcome="fail") == 0
    assert t.counter_value(GATE_RETRIES) == 0


def test_the_metrics_spec_gives_the_first_attempt_pass_rate_formula() -> None:
    text = METRICS_SPEC.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| First-attempt pass rate"))
    formula = (
        '1 \N{MINUS SIGN} `evaluator_calls{outcome="fail"}` / '
        "`evaluator_calls{outcome in pass, fail}`"
    )
    assert formula in row
