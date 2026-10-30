"""Exit criterion 2 (feedback part): the real feedback adapter, `LangfuseFeedback`, passes the same
`FeedbackPortContract` as its fake, `InMemoryFeedback`. Offline, against the fake Langfuse API
(the scores API as the `langfuse` SDK 5.0.4 client sends it). The same suite runs against a real
Langfuse in `packages/chassis/tests/integration/test_langfuse_feedback_contract.py` (`network`).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from chassis.adapters.langfuse import LangfuseFeedback, feedback_from_score
from chassis.fakes.feedback import InMemoryFeedback
from chassis.ports.feedback import Feedback
from chassis_contracts import FeedbackPortContract
from fake_langfuse import FakeLangfuse
from poc07_fb_harness import fake_langfuse, langfuse_port


@pytest.fixture
def fake() -> FakeLangfuse:
    return fake_langfuse()


class TestPoc07FbLangfuseFeedback(FeedbackPortContract):
    """The real adapter, over the fake Langfuse API."""

    @pytest.fixture
    def feedback_port(self, fake: FakeLangfuse) -> LangfuseFeedback:
        return langfuse_port(fake)

    @pytest.fixture
    def read_feedback(self, fake: FakeLangfuse) -> Callable[[str], Feedback | None]:
        def read(feedback_id: str) -> Feedback | None:
            score = fake.scores.get(feedback_id)
            return None if score is None else feedback_from_score(score)

        return read

    @pytest.fixture
    def count_feedback(self, fake: FakeLangfuse) -> Callable[[str], int]:
        return lambda fid: int(fid in fake.scores)

    @pytest.fixture
    def make_unavailable(self, fake: FakeLangfuse) -> Callable[[], None]:
        return lambda: setattr(fake, "unavailable", True)


class TestPoc07FbInMemoryFeedback(FeedbackPortContract):
    """The fake, over the same suite, so both columns show in one run."""

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


def test_poc07_fb_both_bindings_run_every_case_of_the_suite() -> None:
    """Criterion 2: "Each new real adapter passes the same contract suite as its fake"."""
    cases = {n for n in dir(FeedbackPortContract) if n.startswith("test_")}
    assert cases <= set(dir(TestPoc07FbLangfuseFeedback))
    assert cases <= set(dir(TestPoc07FbInMemoryFeedback))
    assert len(cases) >= 5
