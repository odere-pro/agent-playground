"""FeedbackPort: a user's score for one call, written onto the call's trace (PoC-7). The real
adapter is `LangfuseFeedback` (`chassis.adapters.langfuse`), Langfuse's scores API.

`submit(feedback)` is idempotent per `feedback_id`: the same id twice is one record. The chassis
serves it as `POST /v1/feedback` and finds the trace from the `request_id`.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

COMMENT_MAX = 2000
"""suggested: the longest comment."""


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feedback_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    """The W3C trace id of the call (`chassis.core.trace.trace_id_hex`)."""
    score: float = Field(ge=0, le=1)
    """suggested: 0 is bad, 1 is good; a thumbs down is 0, a thumbs up is 1."""
    label: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=COMMENT_MAX)
    subject: str | None = None
    """Who gave it: the caller's `Principal.subject`, never a value from the body."""


class FeedbackError(Exception):
    """The port could not store the feedback. `code`:

    - `feedback_unavailable`: the store did not answer or failed with a 5xx (`retryable=True`).
    - `feedback_rejected`: the store refused it (a 4xx), not retryable.
    - `feedback_not_configured`: `NoFeedback`.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class FeedbackPort(Protocol):
    name: str

    async def submit(self, feedback: Feedback) -> None: ...


class NoFeedback:
    """No feedback store: every call raises `feedback_not_configured`. The `PortBundle` default."""

    name = "none"

    async def submit(self, feedback: Feedback) -> None:
        raise FeedbackError("feedback_not_configured", "spec.adapters.feedback is none")
