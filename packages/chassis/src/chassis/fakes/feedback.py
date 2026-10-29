"""InMemoryFeedback: the `FeedbackPort` fake. One record per `feedback_id`."""

from __future__ import annotations

from chassis.ports.feedback import Feedback, FeedbackError


class InMemoryFeedback:
    name = "memory"

    def __init__(self) -> None:
        self.records: dict[str, Feedback] = {}
        self.unavailable = False

    async def submit(self, feedback: Feedback) -> None:
        if self.unavailable:
            raise FeedbackError("feedback_unavailable", "scripted outage", retryable=True)
        self.records[feedback.feedback_id] = feedback

    def get(self, feedback_id: str) -> Feedback | None:
        return self.records.get(feedback_id)
