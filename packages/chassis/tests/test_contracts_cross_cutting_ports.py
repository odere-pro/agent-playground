"""PoC-7: the fakes of the four new ports pass their contract suites. Real adapters bind the
same classes in their own test files.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from chassis.fakes import (
    InMemoryFeedback,
    KeywordGuardrails,
    ScoreRule,
    ScriptedEvaluator,
    StaticTokens,
)
from chassis.ports.auth import Principal
from chassis.ports.feedback import Feedback
from chassis_contracts import (
    AuthPortContract,
    EvaluatorPortContract,
    FeedbackPortContract,
    GoodToken,
    GuardrailPortContract,
)


class TestStaticTokens(AuthPortContract):
    @pytest.fixture
    def auth_port(self) -> StaticTokens:
        return StaticTokens(
            {"tok-good": Principal(subject="user-1", scopes=frozenset({"agent:run"}))}
        )

    @pytest.fixture
    def good_token(self) -> GoodToken:
        return GoodToken("tok-good", "user-1", frozenset({"agent:run"}))

    @pytest.fixture
    def bad_tokens(self) -> list[str]:
        return ["tok-unknown", "Bearer tok-good", "tok-good "]

    @pytest.fixture
    def make_unavailable(self, auth_port: StaticTokens) -> Callable[[], None]:
        return lambda: setattr(auth_port, "unavailable", True)


class TestKeywordGuardrails(GuardrailPortContract):
    @pytest.fixture
    def guardrail_port(self) -> KeywordGuardrails:
        return KeywordGuardrails()

    @pytest.fixture
    def make_unavailable(self, guardrail_port: KeywordGuardrails) -> Callable[[], None]:
        return lambda: setattr(guardrail_port, "unavailable", True)


class TestScriptedEvaluator(EvaluatorPortContract):
    @pytest.fixture
    def evaluator_port(self) -> ScriptedEvaluator:
        return ScriptedEvaluator([ScoreRule(match="It is cheap", score=0.1)], default=0.9)

    @pytest.fixture
    def make_unavailable(self, evaluator_port: ScriptedEvaluator) -> Callable[[], None]:
        return lambda: setattr(evaluator_port, "unavailable", True)


class TestInMemoryFeedback(FeedbackPortContract):
    @pytest.fixture
    def feedback_port(self) -> InMemoryFeedback:
        return InMemoryFeedback()

    @pytest.fixture
    def read_feedback(self, feedback_port: InMemoryFeedback) -> Callable[[str], Feedback | None]:
        return feedback_port.get

    @pytest.fixture
    def count_feedback(self, feedback_port: InMemoryFeedback) -> Callable[[str], int]:
        return lambda fid: int(fid in feedback_port.records)

    @pytest.fixture
    def make_unavailable(self, feedback_port: InMemoryFeedback) -> Callable[[], None]:
        return lambda: setattr(feedback_port, "unavailable", True)
