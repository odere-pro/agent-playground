"""EvaluatorPortContract (PoC-7): a score is in [0, 1], a good answer scores at or above the
threshold and a bad one below it, the decision repeats, the cost is reported, and an outage is
`evaluator_unavailable` and retryable.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from chassis.ports.evaluator import EvalCase, EvaluatorError, EvaluatorPort

GOOD = EvalCase(
    input="simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent.",
    output="Acme made the SLM in 2026. It cut costs by 30 percent.",
    reference="Keeps 2026, Acme, and 30 percent.",
)
BAD = EvalCase(
    input="simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent.",
    output="It is a model. It is cheap.",
    reference="Keeps 2026, Acme, and 30 percent.",
)


@pytest.mark.contract
class EvaluatorPortContract:
    """Subclass as `Test*`, provide `evaluator_port`. Override `good_case`, `bad_case`, or
    `threshold` when the adapter needs other fixtures. Optional: `make_unavailable`.
    """

    @pytest.fixture
    def evaluator_port(self) -> EvaluatorPort:
        raise NotImplementedError("provide an evaluator_port fixture")

    @pytest.fixture
    def good_case(self) -> EvalCase:
        return GOOD

    @pytest.fixture
    def bad_case(self) -> EvalCase:
        return BAD

    @pytest.fixture
    def threshold(self) -> float:
        return 0.5

    @pytest.fixture
    def make_unavailable(self) -> Callable[[], None]:
        pytest.skip("binding provides no make_unavailable")

    async def test_scores_are_in_range_and_ordered(
        self,
        evaluator_port: EvaluatorPort,
        good_case: EvalCase,
        bad_case: EvalCase,
        threshold: float,
    ) -> None:
        good = await evaluator_port.score(good_case)
        bad = await evaluator_port.score(bad_case)
        assert 0.0 <= bad.value <= 1.0 and 0.0 <= good.value <= 1.0
        assert good.value >= threshold > bad.value

    async def test_the_decision_repeats(
        self, evaluator_port: EvaluatorPort, good_case: EvalCase, threshold: float
    ) -> None:
        first = await evaluator_port.score(good_case)
        second = await evaluator_port.score(good_case)
        assert (first.value >= threshold) == (second.value >= threshold)

    async def test_the_cost_is_reported(
        self, evaluator_port: EvaluatorPort, good_case: EvalCase
    ) -> None:
        score = await evaluator_port.score(good_case)
        assert score.evaluator
        assert score.input_tokens >= 0 and score.output_tokens >= 0
        assert score.latency_ms >= 0
        assert score.cost_usd is None or score.cost_usd >= 0

    async def test_outage_is_retryable(
        self,
        evaluator_port: EvaluatorPort,
        good_case: EvalCase,
        make_unavailable: Callable[[], None],
    ) -> None:
        make_unavailable()
        with pytest.raises(EvaluatorError) as info:
            await evaluator_port.score(good_case)
        assert info.value.code == "evaluator_unavailable"
        assert info.value.retryable is True
