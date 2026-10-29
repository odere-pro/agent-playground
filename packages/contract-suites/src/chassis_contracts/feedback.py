"""FeedbackPortContract (PoC-7): a submitted feedback is stored with its fields on its trace,
the same `feedback_id` twice is one record, and an outage is `feedback_unavailable` and
retryable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from chassis.ports.feedback import Feedback, FeedbackError, FeedbackPort

ReadFeedback = Callable[[str], Feedback | None]
"""The stored record for a `feedback_id`, or None."""
CountFeedback = Callable[[str], int]
"""How many records the store holds for a `feedback_id`."""


def make_feedback(**overrides: object) -> Feedback:
    fields: dict[str, object] = {
        "feedback_id": uuid.uuid4().hex,
        "request_id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "score": 1.0,
        "label": "helpful",
        "comment": "Clear and short.",
        "subject": "user-1",
    }
    fields.update(overrides)
    return Feedback.model_validate(fields)


@pytest.mark.contract
class FeedbackPortContract:
    """Subclass as `Test*`, provide `feedback_port`, `read_feedback`, and `count_feedback`.
    Optional: `make_unavailable`.
    """

    @pytest.fixture
    def feedback_port(self) -> FeedbackPort:
        raise NotImplementedError("provide a feedback_port fixture")

    @pytest.fixture
    def read_feedback(self) -> ReadFeedback:
        raise NotImplementedError("provide a read_feedback fixture")

    @pytest.fixture
    def count_feedback(self) -> CountFeedback:
        raise NotImplementedError("provide a count_feedback fixture")

    @pytest.fixture
    def make_unavailable(self) -> Callable[[], None]:
        pytest.skip("binding provides no make_unavailable")

    async def test_submitted_feedback_is_stored(
        self, feedback_port: FeedbackPort, read_feedback: ReadFeedback
    ) -> None:
        feedback = make_feedback()
        await feedback_port.submit(feedback)
        assert read_feedback(feedback.feedback_id) == feedback

    async def test_optional_fields_may_be_empty(
        self, feedback_port: FeedbackPort, read_feedback: ReadFeedback
    ) -> None:
        feedback = make_feedback(score=0.0, label=None, comment=None, subject=None)
        await feedback_port.submit(feedback)
        assert read_feedback(feedback.feedback_id) == feedback

    async def test_a_timestamp_is_kept(
        self, feedback_port: FeedbackPort, read_feedback: ReadFeedback
    ) -> None:
        feedback = make_feedback(timestamp=datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
        await feedback_port.submit(feedback)
        assert read_feedback(feedback.feedback_id) == feedback

    async def test_same_id_twice_is_one_record(
        self,
        feedback_port: FeedbackPort,
        read_feedback: ReadFeedback,
        count_feedback: CountFeedback,
    ) -> None:
        feedback = make_feedback()
        await feedback_port.submit(feedback)
        await feedback_port.submit(feedback)
        assert count_feedback(feedback.feedback_id) == 1
        assert read_feedback(feedback.feedback_id) == feedback

    async def test_outage_is_retryable(
        self, feedback_port: FeedbackPort, make_unavailable: Callable[[], None]
    ) -> None:
        make_unavailable()
        with pytest.raises(FeedbackError) as info:
            await feedback_port.submit(make_feedback())
        assert info.value.code == "feedback_unavailable"
        assert info.value.retryable is True
