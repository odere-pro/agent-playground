"""EvaluatorPort: a score for one answer (PoC-7, backlog 017 H-4). Two real adapters:
`JudgeEvaluator` (an LLM judge through `ModelPort`) and `EncoderEvaluator` (a small encoder
model behind an HTTP classifier), in `chassis.adapters.evaluators`.

`score(case)` returns a `Score` in [0, 1] with what the scoring cost: tokens, cost, latency. The
evaluator gate (`chassis.server.evaluator_gate`) compares it to `spec.harness.threshold`. The
port does not decide pass or fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EvalCase:
    input: str
    output: str
    reference: str | None = None
    """An expected answer or rubric, when the case has one."""


@dataclass(frozen=True)
class Score:
    value: float
    """In [0, 1]; higher is better."""
    evaluator: str
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_ms: float = 0.0


class EvaluatorError(Exception):
    """The port could not score. `code`, and whether a retry may help:

    - `evaluator_unavailable`: the model or classifier did not answer (`retryable=True`).
    - `evaluator_bad_reply`: it answered something that is not a score (not retryable).
    - `evaluator_not_configured`: no evaluator is set (`NoEvaluator`).
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class EvaluatorPort(Protocol):
    name: str

    async def score(self, case: EvalCase) -> Score: ...


class NoEvaluator:
    """No evaluator: every call raises `evaluator_not_configured`. The `PortBundle` default; the
    gate refuses to start with it."""

    name = "none"

    async def score(self, case: EvalCase) -> Score:
        raise EvaluatorError("evaluator_not_configured", "spec.adapters.evaluator is none")
