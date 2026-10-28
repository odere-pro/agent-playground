"""ScriptedEvaluator: the `EvaluatorPort` fake. The first rule whose `match` is in the answer
gives the score; else `default`. Records each case; can be made unavailable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from chassis.ports.evaluator import EvalCase, EvaluatorError, Score


@dataclass(frozen=True)
class ScoreRule:
    match: str
    score: float


class ScriptedEvaluator:
    name = "fake"

    def __init__(self, rules: Sequence[ScoreRule] = (), default: float = 1.0) -> None:
        self.rules = list(rules)
        self.default = default
        self.cases: list[EvalCase] = []
        self.unavailable = False

    async def score(self, case: EvalCase) -> Score:
        self.cases.append(case)
        if self.unavailable:
            raise EvaluatorError("evaluator_unavailable", "scripted outage", retryable=True)
        value = next((r.score for r in self.rules if r.match in case.output), self.default)
        return Score(value=value, evaluator=self.name, reason="scripted")
